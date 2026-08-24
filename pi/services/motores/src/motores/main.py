"""motores — executa comandos de movimento nos motores de passo NEMA 23.

Assina robo/motores/comando, move os motores via GPIO em thread dedicada
e publica o estado atual em robo/motores/status (retained) após cada comando.

Configuração por variáveis de ambiente (com defaults sensatos):
    MQTT_HOST             (default 127.0.0.1)
    MQTT_PORT             (default 1883)
    HEARTBEAT_INTERVALO_S (default 10)
    VELOCIDADE_PADRAO     (default 60)
    MOTOR1_INVERTIDO      (default false) — inverte sentido do motor 1
    MOTOR2_INVERTIDO      (default false) — inverte sentido do motor 2
    COMANDO_TIMEOUT_S     (default 1.0) — para os motores se o comando de
                          movimento parar de ser repetido (0 desliga; ver vigia.py)
    LOG_LEVEL             (default INFO)

Requer acesso a GPIO via lgpio; o serviço roda como root (ver robo-motores.service).
Sistema: sudo apt install python3-gpiozero python3-lgpio -y
"""
from __future__ import annotations

import logging
import os
import signal
import time
from typing import Any

from robo_common import topics
from robo_common.mqtt_client import MqttService

from .vigia import Vigia

from .stepper import (
    ControladorMotores,
    M1_EN,
    M1_DIR,
    M1_STEP,
    M2_EN,
    M2_DIR,
    M2_STEP,
    Stepper,
)

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("motores")

SERVICO = "motores"
MQTT_HOST = os.environ.get("MQTT_HOST", "127.0.0.1")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
HEARTBEAT_INTERVALO_S = float(os.environ.get("HEARTBEAT_INTERVALO_S", "10"))
VELOCIDADE_PADRAO = int(os.environ.get("VELOCIDADE_PADRAO", "60"))
MOTOR1_INVERTIDO = os.environ.get("MOTOR1_INVERTIDO", "false").lower() == "true"
MOTOR2_INVERTIDO = os.environ.get("MOTOR2_INVERTIDO", "false").lower() == "true"
#: Quanto tempo de silêncio, no meio de um movimento, significa "pare". O app
#: repete o comando a cada 300 ms enquanto o dedo está no botão, então 1 s dá
#: margem para três repetições perdidas antes de o robô parar sozinho.
COMANDO_TIMEOUT_S = float(os.environ.get("COMANDO_TIMEOUT_S", "1.0"))

_ACOES_VALIDAS = {"frente", "tras", "esquerda", "direita", "parar"}

_parar = False


def _tratar_sinal(signum, _frame) -> None:
    global _parar
    logger.info("Sinal %s recebido; encerrando com elegância...", signum)
    _parar = True


def _ao_receber_comando(
    mqtt_svc: MqttService,
    controlador: ControladorMotores,
    vigia: Vigia,
    _topico: str,
    cmd: dict[str, Any],
) -> None:
    acao = cmd.get("acao", "")
    if acao not in _ACOES_VALIDAS:
        logger.warning("Ação de motor inválida: '%s'; ignorando.", acao)
        return

    vigia.comando_recebido(acao, time.monotonic())

    if acao == "parar":
        velocidade = 0
    else:
        velocidade = int(cmd.get("velocidade", VELOCIDADE_PADRAO))
        if not (0 <= velocidade <= 100):
            logger.warning(
                "Velocidade fora de [0,100]: %s; usando padrão %s.", velocidade, VELOCIDADE_PADRAO
            )
            velocidade = VELOCIDADE_PADRAO

    logger.info("Comando motores: acao=%s velocidade=%s", acao, velocidade)
    controlador.aplicar(acao, velocidade)

    mqtt_svc.publish_json(
        topics.MOTORES_STATUS,
        {"acao": acao, "velocidade": velocidade},
        qos=1,
        retain=True,
    )


def main() -> None:
    signal.signal(signal.SIGINT, _tratar_sinal)
    signal.signal(signal.SIGTERM, _tratar_sinal)

    m1 = Stepper(M1_STEP, M1_DIR, M1_EN)
    m2 = Stepper(M2_STEP, M2_DIR, M2_EN)
    controlador = ControladorMotores(
        m1,
        m2,
        invertido_m1=MOTOR1_INVERTIDO,
        invertido_m2=MOTOR2_INVERTIDO,
    )
    controlador.start()

    mqtt_svc = MqttService(
        client_id=SERVICO,
        host=MQTT_HOST,
        port=MQTT_PORT,
        heartbeat_topic=topics.heartbeat(SERVICO),
    )
    vigia = Vigia(COMANDO_TIMEOUT_S)
    mqtt_svc.on(
        topics.MOTORES_COMANDO,
        lambda topico, msg: _ao_receber_comando(mqtt_svc, controlador, vigia, topico, msg),
    )
    mqtt_svc.start()
    logger.info(
        "Serviço motores no ar; aguardando comandos em %s.", topics.MOTORES_COMANDO
    )
    if vigia.ligado:
        logger.info(
            "Vigia ligado: paro os motores se um movimento ficar %.1fs sem ser repetido.",
            COMANDO_TIMEOUT_S,
        )
    else:
        logger.warning(
            "Vigia DESLIGADO (COMANDO_TIMEOUT_S=0): se o controle cair no meio de "
            "um movimento, o robô continua andando."
        )

    proximo_heartbeat = 0.0
    try:
        while not _parar:
            agora = time.monotonic()

            # O laço já roda a cada 0,5 s por causa do heartbeat; a checagem
            # pega carona nele em vez de trazer uma thread só para isso.
            if vigia.expirou(agora):
                logger.warning(
                    "Nenhum comando de movimento há %.1fs — parando os motores. "
                    "O controle caiu, ou o app não está repetindo o comando.",
                    COMANDO_TIMEOUT_S,
                )
                controlador.aplicar("parar", 0)
                mqtt_svc.publish_json(
                    topics.MOTORES_STATUS,
                    {"acao": "parar", "velocidade": 0, "motivo": "sem_comando"},
                    qos=1,
                    retain=True,
                )

            if agora >= proximo_heartbeat:
                mqtt_svc.publish_json(
                    topics.heartbeat(SERVICO),
                    {"servico": SERVICO, "status": "online", "ts": time.time()},
                    qos=0,
                    retain=True,
                )
                proximo_heartbeat = agora + HEARTBEAT_INTERVALO_S
            time.sleep(0.5)
    finally:
        controlador.stop()
        mqtt_svc.stop()


if __name__ == "__main__":
    main()
