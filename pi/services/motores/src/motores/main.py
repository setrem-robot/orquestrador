"""motores — executa os comandos de movimento que chegam pelo MQTT.

Assina `robo/motores/comando`, converte cada comando em velocidade de cada lado
(`cinematica.py`), acelera até ela sem tranco e entrega o resultado a quem sabe
mexer no hardware (`acionamento.py`). Publica o estado em `robo/motores/status`
(retained) a cada mudança.

O serviço em si não sabe o que é um GPIO nem o que é um passo de motor. Essa foi
a mudança de desenho: antes, este arquivo montava dois `Stepper`, um
`ControladorMotores` com thread própria de pulsos, e conversava com MQTT — três
assuntos no mesmo lugar, nenhum deles testável sem o robô montado. Hoje são três
camadas, e as duas de baixo têm teste sem hardware nenhum.

Configuração por variáveis de ambiente:
    MQTT_HOST             (default 127.0.0.1)
    MQTT_PORT             (default 1883)
    HEARTBEAT_INTERVALO_S (default 10)
    VELOCIDADE_PADRAO     (default 60) — usada quando o comando não traz uma
    MOTORES_BACKEND       (default auto) — "auto", "stepper" ou "simulado"
    MOTOR_ESQ_INVERTIDO   (default false) — espelha o lado montado ao contrário
    MOTOR_DIR_INVERTIDO   (default false)
    ACELERACAO            (default 3.0) — do parado ao máximo em 1/3 de segundo.
                          0 desliga a rampa e faz cada comando valer na hora
    COMANDO_TIMEOUT_S     (default 1.0) — para tudo se o movimento parar de ser
                          repetido (0 desliga; ver vigia.py)
    LOG_LEVEL             (default INFO)

Sistema, para o acionamento real: sudo apt install python3-gpiozero python3-lgpio
"""
from __future__ import annotations

import logging
import os
import signal
import time
from typing import Any

from robo_common import topics
from robo_common.mqtt_client import MqttService

from .acionamento import Acionamento, criar_acionamento
from .cinematica import ACOES_VALIDAS, Rampa, Velocidades, do_comando
from .vigia import Vigia

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("motores")


def _booleano(*nomes: str, padrao: str = "false") -> bool:
    """Primeiro nome definido no ambiente vence.

    Os nomes antigos (MOTOR1/MOTOR2) continuam valendo: quem já tem a unidade
    systemd instalada não pode perder a calibração do chassi só por atualizar.
    """
    for nome in nomes:
        valor = os.environ.get(nome)
        if valor is not None:
            return valor.strip().lower() in {"1", "true", "yes", "on"}
    return padrao.lower() in {"1", "true", "yes", "on"}


SERVICO = "motores"
MQTT_HOST = os.environ.get("MQTT_HOST", "127.0.0.1")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
HEARTBEAT_INTERVALO_S = float(os.environ.get("HEARTBEAT_INTERVALO_S", "10"))
VELOCIDADE_PADRAO = int(os.environ.get("VELOCIDADE_PADRAO", "60"))
BACKEND = os.environ.get("MOTORES_BACKEND", "auto")
ESQ_INVERTIDO = _booleano("MOTOR_ESQ_INVERTIDO", "MOTOR1_INVERTIDO")
DIR_INVERTIDO = _booleano("MOTOR_DIR_INVERTIDO", "MOTOR2_INVERTIDO")
ACELERACAO = float(os.environ.get("ACELERACAO", "3.0"))
#: Quanto tempo de silêncio, no meio de um movimento, significa "pare". O app
#: repete o comando a cada 300 ms enquanto o dedo está no botão, então 1 s dá
#: margem para três repetições perdidas antes de o robô parar sozinho.
COMANDO_TIMEOUT_S = float(os.environ.get("COMANDO_TIMEOUT_S", "1.0"))

#: Volta do laço. Precisa ser bem menor que a rampa inteira, senão a aceleração
#: sai em degraus em vez de subida; 50 ms dão seis passos numa rampa de um terço
#: de segundo e continuam custando quase nada de CPU.
TICK_S = 0.05

_parar = False


def _tratar_sinal(signum, _frame) -> None:
    global _parar
    logger.info("Sinal %s recebido; encerrando com elegância...", signum)
    _parar = True


class ServicoMotores:
    """Junta a rampa, o vigia e o acionamento num só objeto com estado.

    Fica separado de `main()` para poder ser exercitado com `tick()` num teste:
    sem broker, sem GPIO e sem esperar em tempo real.
    """

    def __init__(
        self,
        acionamento: Acionamento,
        vigia: Vigia,
        *,
        aceleracao: float = ACELERACAO,
        velocidade_padrao: int = VELOCIDADE_PADRAO,
        timeout_s: float = COMANDO_TIMEOUT_S,
        publicar_status=None,
    ) -> None:
        self._acionamento = acionamento
        self._vigia = vigia
        self._rampa = Rampa(aceleracao)
        self._velocidade_padrao = velocidade_padrao
        self._timeout_s = timeout_s
        self._publicar = publicar_status
        self._ultimo_status: dict[str, Any] | None = None

    # -- entrada -----------------------------------------------------------
    def receber(self, comando: dict[str, Any], agora: float) -> None:
        """Aceita um comando do contrato MQTT e o transforma em alvo."""
        acao = str(comando.get("acao", ""))
        if acao not in ACOES_VALIDAS:
            logger.warning("Ação de motor inválida: '%s'; ignorando.", acao)
            return

        try:
            alvo = do_comando(
                acao,
                int(comando.get("velocidade", self._velocidade_padrao)),
                linear=float(comando.get("linear", 0.0)),
                angular=float(comando.get("angular", 0.0)),
            )
        except (TypeError, ValueError) as exc:
            # Comando malformado é lixo de transmissão ou app desatualizado.
            # Nenhum dos dois pode derrubar o serviço que segura os motores.
            logger.warning("Comando de motor recusado (%s): %s", exc, comando)
            return

        if alvo.parado:
            # Parada pedida é parada agora: quem apertou "parar" não quer ver o
            # robô desacelerando por mais meio segundo.
            self._vigia.parada_recebida()
            self._acionamento.aplicar(self._rampa.parar_agora())
        else:
            self._vigia.movimento_recebido(agora)
            self._rampa.pedir(alvo)

        logger.info("Comando: %s -> esquerda=%.2f direita=%.2f", acao, alvo.esquerda, alvo.direita)
        self._anunciar(acao, alvo)

    def parada_de_emergencia(self, motivo: str) -> None:
        """Para tudo sem rampa, e conta o porquê."""
        logger.warning("Parada de emergência: %s", motivo)
        self._vigia.parada_recebida()
        self._acionamento.aplicar(self._rampa.parar_agora())
        self._anunciar("parar", Velocidades(), motivo=motivo)

    # -- tempo -------------------------------------------------------------
    def tick(self, dt: float, agora: float) -> None:
        """Uma volta do laço: vigia o silêncio e avança a rampa."""
        if self._vigia.expirou(agora):
            self.parada_de_emergencia(
                f"nenhum comando de movimento há {self._timeout_s:.1f}s — o controle "
                "caiu, ou o app não está repetindo o comando"
            )
            return
        self._acionamento.aplicar(self._rampa.avancar(dt))

    def encerrar(self) -> None:
        self._acionamento.parar()
        self._acionamento.fechar()

    # -- saída -------------------------------------------------------------
    def _anunciar(self, acao: str, alvo: Velocidades, *, motivo: str = "") -> None:
        status: dict[str, Any] = {
            "acao": acao,
            # Mantido para o app e para a telemetria, que já leem este campo:
            # é a intensidade do lado mais rápido, de 0 a 100.
            "velocidade": round(100 * max(abs(alvo.esquerda), abs(alvo.direita))),
            "esquerda": round(alvo.esquerda, 3),
            "direita": round(alvo.direita, 3),
        }
        if motivo:
            status["motivo"] = motivo
        # Repetir o mesmo status a cada comando repetido encheria o tópico
        # retido e a telemetria com a mesma linha três vezes por segundo.
        if status == self._ultimo_status:
            return
        self._ultimo_status = status
        if self._publicar is not None:
            self._publicar(status)


def main() -> None:
    signal.signal(signal.SIGINT, _tratar_sinal)
    signal.signal(signal.SIGTERM, _tratar_sinal)

    acionamento = criar_acionamento(
        BACKEND, invertido_esquerda=ESQ_INVERTIDO, invertido_direita=DIR_INVERTIDO
    )
    vigia = Vigia(COMANDO_TIMEOUT_S)

    mqtt_svc = MqttService(
        client_id=SERVICO,
        host=MQTT_HOST,
        port=MQTT_PORT,
        heartbeat_topic=topics.heartbeat(SERVICO),
    )

    def publicar(status: dict[str, Any]) -> None:
        mqtt_svc.publish_json(topics.MOTORES_STATUS, status, qos=1, retain=True)

    servico = ServicoMotores(acionamento, vigia, publicar_status=publicar)

    mqtt_svc.on(
        topics.MOTORES_COMANDO,
        lambda _topico, msg: servico.receber(msg, time.monotonic()),
    )
    mqtt_svc.start()
    logger.info("Serviço motores no ar; aguardando comandos em %s.", topics.MOTORES_COMANDO)
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
    anterior = time.monotonic()
    try:
        while not _parar:
            agora = time.monotonic()
            servico.tick(agora - anterior, agora)
            anterior = agora

            if agora >= proximo_heartbeat:
                mqtt_svc.publish_json(
                    topics.heartbeat(SERVICO),
                    {"servico": SERVICO, "status": "online", "ts": time.time()},
                    qos=0,
                    retain=True,
                )
                proximo_heartbeat = agora + HEARTBEAT_INTERVALO_S
            time.sleep(TICK_S)
    finally:
        servico.encerrar()
        mqtt_svc.stop()


if __name__ == "__main__":
    main()
