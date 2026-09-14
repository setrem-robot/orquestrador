"""ingestor cloud — broker MQTT remoto -> TimescaleDB.

Roda na MESMA VM do broker remoto. Assina robo/telemetria/# (tudo que a
bridge do Pi replicou para a nuvem) e grava cada mensagem numa hypertable do
TimescaleDB. É o ponto onde a telemetria efêmera vira histórico consultável.

Mantém-se deliberadamente independente da lib roboCommon do Pi: a nuvem é um
ecossistema separado e auto-contido. A única "interface" compartilhada é o
nome do tópico (robo/telemetria/<tipo>), que é estável.

Configuração por variáveis de ambiente:
    MQTT_HOST       (default mosquitto)        broker remoto na rede do compose
    MQTT_PORT       (default 1883)
    MQTT_USERNAME   (obrigatório; broker remoto exige autenticação)
    MQTT_PASSWORD   (obrigatório)
    MQTT_TOPIC      (default robo/telemetria/#)
    PGHOST          (default timescaledb)
    PGPORT          (default 5432)
    PGUSER          (default robo)
    PGPASSWORD      (obrigatório)
    PGDATABASE      (default robo)
    LOG_LEVEL       (default INFO)
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt
import psycopg

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("ingestor")

# --- Configuração ---
MQTT_HOST = os.environ.get("MQTT_HOST", "mosquitto")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USERNAME = os.environ.get("MQTT_USERNAME")
MQTT_PASSWORD = os.environ.get("MQTT_PASSWORD")
MQTT_TOPIC = os.environ.get("MQTT_TOPIC", "robo/telemetria/#")

PG_CONNINFO = (
    f"host={os.environ.get('PGHOST', 'timescaledb')} "
    f"port={os.environ.get('PGPORT', '5432')} "
    f"user={os.environ.get('PGUSER', 'robo')} "
    f"password={os.environ.get('PGPASSWORD', '')} "
    f"dbname={os.environ.get('PGDATABASE', 'robo')}"
)

INSERT_SQL = (
    "INSERT INTO telemetria (ts, tipo, topico, payload) VALUES (%s, %s, %s, %s)"
)


def conectarBanco() -> psycopg.Connection:
    """Abre a conexão com o TimescaleDB, esperando o banco subir.

    No `docker compose up` o ingestor pode iniciar antes do banco aceitar
    conexões (mesmo com depends_on/healthcheck há a janela do init SQL).
    """
    while True:
        try:
            conn = psycopg.connect(PG_CONNINFO, autocommit=True)
            logger.info("Conectado ao TimescaleDB.")
            return conn
        except psycopg.OperationalError as exc:
            logger.warning("Banco indisponível (%s). Nova tentativa em 3s.", exc)
            time.sleep(3)


def tipoDoTopico(topico: str) -> str:
    """Extrai o <tipo> de robo/telemetria/<tipo> (último segmento)."""
    return topico.rsplit("/", 1)[-1] or "desconhecido"


def timestampDoPayload(payload: dict) -> datetime:
    """Usa o campo 'ts' (epoch em segundos) do payload, se presente e válido.

    Preferimos o instante em que o dado foi GERADO no robô; só caímos para o
    horário de chegada (now) quando o produtor não carimbou o tempo.
    """
    ts = payload.get("ts")
    if isinstance(ts, (int, float)):
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            pass
    return datetime.now(tz=timezone.utc)


class Ingestor:
    def __init__(self) -> None:
        self.conn = conectarBanco()
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="ingestor")
        if MQTT_USERNAME:
            self.client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)
        self.client.on_connect = self.onConnect
        self.client.on_message = self.onMessage

    def onConnect(self, client, userdata, flags, reasonCode, properties):
        if reasonCode != 0:
            logger.error("Falha ao conectar no broker remoto (rc=%s).", reasonCode)
            return
        logger.info("Conectado ao broker remoto; assinando %s", MQTT_TOPIC)
        client.subscribe(MQTT_TOPIC, qos=1)

    def onMessage(self, client, userdata, message):
        try:
            payload = json.loads(message.payload.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.warning("Mensagem ignorada (JSON inválido) em %s.", message.topic)
            return
        if not isinstance(payload, dict):
            logger.warning("Payload ignorado (não é objeto) em %s.", message.topic)
            return

        registro = (
            timestampDoPayload(payload),
            tipoDoTopico(message.topic),
            message.topic,
            json.dumps(payload),
        )
        self.gravar(registro)

    def gravar(self, registro: tuple) -> None:
        # Uma reconexão ao banco cobre o caso de o Postgres reiniciar embaixo
        # de nós; uma única retentativa evita perder a mensagem nesse caso.
        for tentativa in (1, 2):
            try:
                with self.conn.cursor() as cur:
                    cur.execute(INSERT_SQL, registro)
                logger.info("Gravado: tipo=%s topico=%s", registro[1], registro[2])
                return
            except psycopg.Error as exc:
                logger.warning(
                    "Erro ao gravar (tentativa %d): %s. Reconectando ao banco...",
                    tentativa, exc,
                )
                try:
                    self.conn.close()
                except Exception:
                    pass
                self.conn = conectarBanco()
        logger.error("Mensagem descartada após falha de gravação: %s", registro[2])

    def run(self) -> None:
        self.client.connect(MQTT_HOST, MQTT_PORT, keepalive=30)
        # loop_forever cuida da reconexão automática ao broker.
        self.client.loop_forever()


def main() -> None:
    Ingestor().run()


if __name__ == "__main__":
    main()
