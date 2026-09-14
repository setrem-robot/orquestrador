"""wifi — aplica a configuração de Wi-Fi recebida pelo barramento de comandos.

O Pi NÃO fala Bluetooth: o ESP32 é o único gateway. A credencial chega como
qualquer outro comando — app -> ESP32 (Bluetooth) -> serial -> serialIngestor
-> robo/comando/entrada -> o orquestrador roteia {"tipo":"wifi"} para
robo/wifi/comando. Este serviço assina esse tópico, aplica com o nmcli e
publica o estado da conexão em robo/sistema/wifi (que o orquestrador espelha
para a nuvem).

Como precisa mexer no NetworkManager, roda como root (ver robo-wifi.service).

Configuração por variáveis de ambiente (com defaults sensatos):
    WIFI_IFACE            (default wlan0)
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

from roboCommon import topics
from roboCommon.mqttClient import MqttService

from . import rede

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("wifi")

# --- Configuração ---
SERVICO = "wifi"
WIFI_IFACE = os.environ.get("WIFI_IFACE", "wlan0")
MQTT_HOST = os.environ.get("MQTT_HOST", "127.0.0.1")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
HEARTBEAT_INTERVALO_S = float(os.environ.get("HEARTBEAT_INTERVALO_S", "10"))

parar = False


def tratarSinal(signum, frame) -> None:
    global parar
    logger.info("Sinal %s recebido; encerrando com elegância...", signum)
    parar = True


def publicarStatusWifi(mqttSvc: MqttService, resposta: dict[str, Any]) -> None:
    """Espelha no MQTT o estado da conexão após um conectar/status bem-sucedido."""
    if not resposta.get("ok") or resposta.get("acao") not in ("conectar", "status"):
        return
    mqttSvc.publishJson(
        topics.SISTEMA_WIFI,
        {
            "conectado": resposta.get("conectado", True),
            "ssid": resposta.get("ssid"),
            "ip": resposta.get("ip"),
            "ts": time.time(),
        },
        qos=1,
        retain=True,
    )


def aoReceberComando(
    mqttSvc: MqttService, topico: str, comando: dict[str, Any]
) -> None:
    """Handler de robo/wifi/comando: aplica a ação e publica o estado."""
    resposta = rede.processar(comando, WIFI_IFACE)
    if resposta.get("ok"):
        logger.info("Comando wifi '%s' aplicado: %s", resposta.get("acao"), resposta)
    else:
        logger.warning("Comando wifi falhou: %s", resposta)
    publicarStatusWifi(mqttSvc, resposta)


def main() -> None:
    signal.signal(signal.SIGINT, tratarSinal)
    signal.signal(signal.SIGTERM, tratarSinal)

    mqttSvc = MqttService(
        clientId=SERVICO,
        host=MQTT_HOST,
        port=MQTT_PORT,
        heartbeatTopic=topics.heartbeat(SERVICO),
    )
    mqttSvc.on(
        topics.WIFI_COMANDO,
        lambda topico, msg: aoReceberComando(mqttSvc, topico, msg),
    )
    mqttSvc.start()
    logger.info("Serviço wifi no ar; aguardando comandos em %s.", topics.WIFI_COMANDO)

    # Ao subir, publica o estado atual da rede (útil para o dashboard/nuvem).
    publicarStatusWifi(
        mqttSvc, {"ok": True, "acao": "status", **rede.statusAtual(WIFI_IFACE)}
    )

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
            # O trabalho real acontece no callback (thread do paho); aqui só
            # mantemos o processo vivo e responsivo a parada/heartbeat.
            time.sleep(0.5)
    finally:
        mqttSvc.stop()


if __name__ == "__main__":
    main()
