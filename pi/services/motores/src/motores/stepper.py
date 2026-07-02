"""Controle de baixo nível dos motores de passo NEMA 23 via GPIO.

Dois drivers TMC2209 em modo STEP/DIR com 1/8 microstep (MS1=LOW, MS2=LOW).
O EN é active-low: EN=LOW habilita o driver; EN=HIGH desabilita.

    MOTOR 1                MOTOR 2
    GPIO 20 → STEP         GPIO 19 → STEP
    GPIO 21 → DIR          GPIO 26 → DIR
    GPIO 16 → EN           GPIO 13 → EN

Comum: Pi 3V3 → VIO de cada driver; Pi GND ↔ GND da fonte 12V (comum obrigatório).
"""
from __future__ import annotations

import logging
import threading
import time

from gpiozero import DigitalOutputDevice

logger = logging.getLogger(__name__)

# Pinos BCM
M1_STEP, M1_DIR, M1_EN = 20, 21, 16
M2_STEP, M2_DIR, M2_EN = 19, 26, 13

# 200 passos/volta × 1/8 microstep
PASSOS_POR_VOLTA = 200 * 8

# Mapeamento velocidade [1, 100] → delay entre flancos do pulso STEP
_DELAY_MIN_S = 0.0005   # velocidade 100 → mais rápido (~1000 steps/s)
_DELAY_MAX_S = 0.005    # velocidade 1   → mais lento (~100 steps/s)

# Sentidos por ação: (sentido_horario_m1, sentido_horario_m2)
# Robô diferencial com motores montados espelhados; ajuste via MOTOR*_INVERTIDO
# se o robô andar para o lado errado.
_SENTIDOS: dict[str, tuple[bool, bool]] = {
    "frente":   (True,  True),
    "tras":     (False, False),
    "esquerda": (False, True),
    "direita":  (True,  False),
}


def _delay_para_velocidade(velocidade: int) -> float:
    v = max(1, min(100, velocidade))
    return _DELAY_MAX_S - (v - 1) / 99.0 * (_DELAY_MAX_S - _DELAY_MIN_S)


class Stepper:
    """Motor de passo com driver TMC2209 (STEP/DIR/EN active-low)."""

    def __init__(self, step_pin: int, dir_pin: int, enable_pin: int) -> None:
        self._step = DigitalOutputDevice(step_pin)
        self._dir = DigitalOutputDevice(dir_pin)
        # active-low: initial_value=True → HIGH → motor desabilitado ao iniciar
        self._en = DigitalOutputDevice(enable_pin, initial_value=True)

    def ativar(self) -> None:
        self._en.off()   # LOW = habilitado

    def desativar(self) -> None:
        self._en.on()    # HIGH = desabilitado

    def set_sentido(self, sentido_horario: bool) -> None:
        self._dir.value = 1 if sentido_horario else 0
        time.sleep(0.001)   # setup time exigido pelo TMC2209

    def pulso_on(self) -> None:
        self._step.on()

    def pulso_off(self) -> None:
        self._step.off()


class ControladorMotores:
    """Move dois motores de passo em thread dedicada.

    Recebe comandos de alto nível (frente/tras/esquerda/direita/parar) e
    pulsa os GPIOs continuamente enquanto houver movimento. Thread-safe:
    `aplicar()` pode ser chamado de qualquer thread (ex: callback MQTT).
    """

    def __init__(
        self,
        m1: Stepper,
        m2: Stepper,
        invertido_m1: bool = False,
        invertido_m2: bool = False,
    ) -> None:
        self._m1 = m1
        self._m2 = m2
        self._inv1 = invertido_m1
        self._inv2 = invertido_m2

        self._acao = "parar"
        self._delay = _delay_para_velocidade(60)
        self._lock = threading.Lock()
        self._evento = threading.Event()
        self._encerrar = False
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="motores-thread"
        )

    def start(self) -> None:
        self._thread.start()
        logger.info("Controlador de motores iniciado.")

    def stop(self) -> None:
        self._encerrar = True
        self._evento.set()
        self._thread.join(timeout=5)
        self._m1.desativar()
        self._m2.desativar()
        logger.info("Controlador de motores encerrado.")

    def aplicar(self, acao: str, velocidade: int) -> None:
        """Atualiza o comando de movimento. Thread-safe."""
        with self._lock:
            self._acao = acao
            self._delay = _delay_para_velocidade(velocidade)
        self._evento.set()

    # ------------------------------------------------------------------
    # Thread de movimento — lê o estado desejado e pulsa os GPIOs
    # ------------------------------------------------------------------
    def _loop(self) -> None:
        ativos = False
        s1_atual: bool | None = None
        s2_atual: bool | None = None

        while not self._encerrar:
            with self._lock:
                acao = self._acao
                delay = self._delay

            if acao == "parar":
                if ativos:
                    self._m1.desativar()
                    self._m2.desativar()
                    ativos = False
                    s1_atual = s2_atual = None
                # Aguarda novo comando; timeout evita travar se o evento for perdido.
                self._evento.wait(timeout=0.5)
                self._evento.clear()
                continue

            sentidos = _SENTIDOS.get(acao)
            if sentidos is None:
                logger.warning("Ação desconhecida '%s'; parando motores.", acao)
                with self._lock:
                    self._acao = "parar"
                continue

            s1 = sentidos[0] ^ self._inv1
            s2 = sentidos[1] ^ self._inv2

            if not ativos:
                self._m1.ativar()
                self._m2.ativar()
                ativos = True

            # Atualiza direção só quando muda (cada set_sentido dorme 1 ms).
            if s1 != s1_atual:
                self._m1.set_sentido(s1)
                s1_atual = s1
            if s2 != s2_atual:
                self._m2.set_sentido(s2)
                s2_atual = s2

            # Pulso simultâneo nos dois motores (Bresenham seria necessário só
            # para passos diferentes por motor; aqui ambos pulsam igual).
            self._m1.pulso_on()
            self._m2.pulso_on()
            time.sleep(delay)
            self._m1.pulso_off()
            self._m2.pulso_off()
            time.sleep(delay)
