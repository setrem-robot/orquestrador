"""orquestrador — o "cérebro" de roteamento do robô.

Duas responsabilidades, ambas ligadas ao barramento MQTT local:

  1. ROTEAR COMANDOS: assina robo/comando/entrada (tudo que o app mandou via
     Bluetooth) e encaminha cada comando para o tópico do grupo responsável
     (motores, voz, ...). A tradução fica em roteador.py.

  2. ESPELHAR TELEMETRIA: assina os tópicos "vivos" de cada domínio (posição,
     status dos motores, bateria) e republica o que deve ser PERSISTIDO sob
     robo/telemetria/*. A bridge do Mosquitto replica esse prefixo para a
     nuvem. Centralizar essa decisão aqui evita que cada serviço precise saber
     o que vai (ou não) para o histórico.

Configuração por variáveis de ambiente (com defaults sensatos):
    MQTT_HOST             (default 127.0.0.1)
    MQTT_PORT             (default 1883)
    HEARTBEAT_INTERVALO_S (default 10)
    LOG_LEVEL             (default INFO)
"""

from __future__ import annotations

import logging
import os
import signal
import time
from typing import Any

from robo_common import topics
from robo_common.mqttClient import MqttService

from .roteador import rotear

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("orquestrador")

# --- Configuração ---
SERVICO = "orquestrador"
MQTT_HOST = os.environ.get("MQTT_HOST", "127.0.0.1")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
HEARTBEAT_INTERVALO_S = float(os.environ.get("HEARTBEAT_INTERVALO_S", "10"))

# Tópicos "vivos" que devem ser espelhados para a nuvem, e sob qual nome.
# (origem -> destino em robo/telemetria/<tipo>). Acrescentar uma fonte de
# histórico é só registrar mais uma linha aqui.
ESPELHO_TELEMETRIA = {
    topics.GPS_POSICAO: topics.telemetria("gps"),
    topics.MOTORES_STATUS: topics.telemetria("motores"),
    topics.SISTEMA_BATERIA: topics.telemetria("bateria"),
    topics.SISTEMA_WIFI: topics.telemetria("wifi"),
}

# Flag de parada, acionada por SIGINT/SIGTERM (systemd manda SIGTERM ao parar).
parar = False


def tratarSinal(signum, frame) -> None:
    global parar
    logger.info("Sinal %s recebido; encerrando com elegância...", signum)
    parar = True


def aoReceberComando(mqttSvc: MqttService, topico: str, comando: dict[str, Any]) -> None:
    """Handler de robo/comando/entrada: roteia e publica."""
    publicacoes = rotear(comando)
    if not publicacoes:
        return
    for destino, payload in publicacoes:
        mqttSvc.publishJson(destino, payload, qos=1)
        logger.info("Comando %s roteado -> %s: %s", comando.get("tipo"), destino, payload)


def aoReceberTelemetria(
    mqttSvc: MqttService, destino: str, topico: str, payload: dict[str, Any]
) -> None:
    """Handler genérico de espelhamento: republica o payload em telemetria/*.

    Usa retain para que um consumidor recém-conectado (ou a nuvem, ao
    reconectar a bridge) receba imediatamente o último valor conhecido.
    """
    mqttSvc.publishJson(destino, payload, qos=1, retain=True)


def main() -> None:
    signal.signal(signal.SIGINT, tratarSinal)
    signal.signal(signal.SIGTERM, tratarSinal)

    mqttSvc = MqttService(
        clientId=SERVICO,
        host=MQTT_HOST,
        port=MQTT_PORT,
        heartbeatTopic=topics.heartbeat(SERVICO),
    )

    # Roteamento de comandos.
    mqttSvc.on(
        topics.COMANDO_ENTRADA,
        lambda topico, msg: aoReceberComando(mqttSvc, topico, msg),
    )

    # Espelhamento de telemetria: um handler por fonte, fixando o destino.
    for origem, destino in ESPELHO_TELEMETRIA.items():
        mqttSvc.on(
            origem,
            lambda topico, msg, destino=destino: aoReceberTelemetria(
                mqttSvc, destino, topico, msg
            ),
        )

    mqttSvc.start()
    logger.info("Orquestrador no ar. Roteando comandos e espelhando telemetria.")

    proximoHeartbeat = 0.0
    try:
        while not parar:
            agora = time.monotonic()
            if agora >= proximoHeartbeat:
                mqttSvc.publishJson(
                    topics.heartbeat(SERVICO),
                    {"servico": SERVICO, "status": "online", "ts": time.time()},
                    qos=0,
                    retain=True,
                )
                proximoHeartbeat = agora + HEARTBEAT_INTERVALO_S
            # O trabalho real acontece nos callbacks (thread do paho); aqui só
            # mantemos o processo vivo e responsivo a parada/heartbeat.
            time.sleep(0.5)
    finally:
        mqttSvc.stop()


if __name__ == "__main__":
    main()
