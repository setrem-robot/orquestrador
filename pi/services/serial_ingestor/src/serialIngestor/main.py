"""serialIngestor — ponte ESP32 (serial) -> MQTT local.

Responsabilidade ÚNICA: ler linhas NDJSON que o ESP32 envia pela serial,
validar que são JSON, e publicar cada uma no tópico de entrada de comandos
do broker local. NÃO interpreta nem roteia comandos — isso é do orquestrador.

Configuração por variáveis de ambiente (com defaults sensatos):
    SERIAL_PORT            (default /dev/ttyUSB0)
    SERIAL_BAUD            (default 115200; precisa bater com o ESP32)
    MQTT_HOST             (default 127.0.0.1)
    MQTT_PORT             (default 1883)
    HEARTBEAT_INTERVALO_S (default 10)
    LOG_LEVEL             (default INFO)
"""

from __future__ import annotations

import json
import logging
import os
import signal
import time

import serial  # pyserial

from roboCommon import topics
from roboCommon.mqttClient import MqttService

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("serialIngestor")

# --- Configuração ---
SERVICO = "serialIngestor"
SERIAL_PORT = os.environ.get("SERIAL_PORT", "/dev/ttyUSB0")
SERIAL_BAUD = int(os.environ.get("SERIAL_BAUD", "115200"))
MQTT_HOST = os.environ.get("MQTT_HOST", "127.0.0.1")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
HEARTBEAT_INTERVALO_S = float(os.environ.get("HEARTBEAT_INTERVALO_S", "10"))

# Flag de parada, acionada por SIGINT/SIGTERM (systemd manda SIGTERM ao parar).
parar = False


def tratarSinal(signum, frame) -> None:
    global parar
    logger.info("Sinal %s recebido; encerrando com elegância...", signum)
    parar = True


def abrirSerial() -> serial.Serial:
    """Abre a serial, reabrindo até conseguir.

    O ESP32 pode ainda não estar pronto no boot, ou ter sido reconectado.
    O timeout de 1s faz o readline() retornar periodicamente, o que mantém
    o loop principal responsivo a heartbeat e à flag de parada.
    """
    while not parar:
        try:
            ser = serial.Serial(SERIAL_PORT, SERIAL_BAUD, timeout=1.0)
            logger.info("Serial aberta em %s @ %d baud.", SERIAL_PORT, SERIAL_BAUD)
            return ser
        except (serial.SerialException, OSError) as exc:
            logger.warning(
                "Não consegui abrir %s (%s). Nova tentativa em 3s.",
                SERIAL_PORT, exc,
            )
            time.sleep(3)
    raise SystemExit(0)


def processarLinha(linha: str, mqttSvc: MqttService) -> None:
    linha = linha.strip()
    if not linha:
        return

    # Defesa em profundidade: o ESP32 já valida o JSON, mas o cabo serial
    # pode corromper bytes. Linha inválida é descartada, não derruba nada.
    try:
        payload = json.loads(linha)
    except json.JSONDecodeError:
        logger.warning("Linha descartada (JSON inválido): %r", linha[:120])
        return

    # O ingestor é um ADAPTADOR: só coloca o comando no barramento.
    # Quem decide o que fazer com ele é o orquestrador (assina COMANDO_ENTRADA).
    mqttSvc.publishJson(topics.COMANDO_ENTRADA, payload, qos=1)
    logger.info("Publicado em %s: %s", topics.COMANDO_ENTRADA, payload)


def main() -> None:
    signal.signal(signal.SIGINT, tratarSinal)
    signal.signal(signal.SIGTERM, tratarSinal)

    mqttSvc = MqttService(
        clientId=SERVICO,
        host=MQTT_HOST,
        port=MQTT_PORT,
        heartbeatTopic=topics.heartbeat(SERVICO),
    )
    mqttSvc.start()

    ser = abrirSerial()
    proximoHeartbeat = 0.0

    try:
        while not parar:
            # Heartbeat periódico ("estou vivo") — complementa o LWT do broker.
            agora = time.monotonic()
            if agora >= proximoHeartbeat:
                mqttSvc.publishJson(
                    topics.heartbeat(SERVICO),
                    {"servico": SERVICO, "status": "online", "ts": time.time()},
                    qos=0,
                    retain=True,
                )
                proximoHeartbeat = agora + HEARTBEAT_INTERVALO_S

            try:
                raw = ser.readline()  # b"" quando dá timeout (1s)
            except (serial.SerialException, OSError) as exc:
                logger.warning("Erro de leitura na serial (%s). Reabrindo...", exc)
                try:
                    ser.close()
                except Exception:
                    pass
                ser = abrirSerial()
                continue

            if not raw:
                continue  # timeout: volta ao loop (permite heartbeat e parada)

            try:
                linha = raw.decode("utf-8")
            except UnicodeDecodeError:
                logger.warning("Bytes não-UTF8 na serial; descartando.")
                continue

            processarLinha(linha, mqttSvc)
    finally:
        try:
            ser.close()
        except Exception:
            pass
        mqttSvc.stop()


if __name__ == "__main__":
    main()
