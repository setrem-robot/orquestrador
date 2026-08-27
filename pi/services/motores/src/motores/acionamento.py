"""Quem transforma velocidade em movimento de verdade.

Uma velocidade de cada lado (ver `cinematica.py`) precisa virar pulso elétrico.
*Como* isso acontece depende do hardware: hoje são dois motores de passo com
driver TMC2209, amanhã podem ser motores com ponte H, ou nenhum motor — só um
registro do que teria sido feito, para desenvolver sem o robô na mesa.

Por isso a fronteira: `Acionamento` é uma ABC, e cada hardware é uma subclasse.
O serviço fala com a ABC e nunca sabe qual está do outro lado. Trocar o chassi
não toca em MQTT, em vigia, nem em cinemática.

## A mudança que mais importa aqui: o pulso deixou de ser feito em Python

A versão anterior gerava cada pulso do STEP à mão, numa thread:

    self._m1.pulso_on(); time.sleep(delay)
    self._m1.pulso_off(); time.sleep(delay)

Duas coisas quebram nesse desenho. A primeira é a precisão: `time.sleep` de meio
milissegundo não dorme meio milissegundo — dorme o que o escalonador do Linux
resolver, e a variação vai para o motor como tremor. A segunda é o custo: mil
passos por segundo são duas mil voltas de laço por segundo num interpretador que
também está desenhando uma face e ouvindo um microfone.

Aqui o pino STEP recebe uma **onda quadrada gerada fora do Python** (PWM do
gpiozero, que no Pi 5 desce até o `lgpio`). A frequência é a quantidade de
passos por segundo, e mudar a velocidade é escrever um número. O motor continua
girando sozinho enquanto ninguém mexer nele — sem thread, sem laço, sem sleep.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from .cinematica import Velocidades

logger = logging.getLogger(__name__)

# Pinos BCM. Dois drivers TMC2209 em STEP/DIR com 1/8 de microstep
# (MS1=LOW, MS2=LOW). EN é active-low: LOW habilita o driver.
#
#     ESQUERDA               DIREITA
#     GPIO 20 → STEP         GPIO 19 → STEP
#     GPIO 21 → DIR          GPIO 26 → DIR
#     GPIO 16 → EN           GPIO 13 → EN
#
# Comum: 3V3 do Pi → VIO de cada driver; GND do Pi ↔ GND da fonte de 12 V
# (o terra comum não é opcional: sem ele o driver lê o pulso como ruído).
ESQ_STEP, ESQ_DIR, ESQ_EN = 20, 21, 16
DIR_STEP, DIR_DIR, DIR_EN = 19, 26, 13

#: 200 passos por volta × 1/8 de microstep.
PASSOS_POR_VOLTA = 200 * 8

#: Faixa de passos por segundo, do mínimo ao máximo de velocidade. São os
#: mesmos números do controle anterior (que os expressava como atraso entre
#: flancos, de 5 ms a 0,5 ms) — de propósito: o pulso passou a ser gerado de
#: outro jeito, e misturar isso com uma mudança na física do robô tornaria
#: impossível saber qual das duas coisas causou o que se vê no chão. Com o
#: pulso fora do Python há folga para subir bem mais; suba medindo, com o robô
#: no chão e carregado.
PASSOS_S_MIN = 100.0
PASSOS_S_MAX = 1000.0


def passos_por_segundo(velocidade: float) -> float:
    """Converte velocidade (0 a 1, em módulo) em frequência do pulso STEP."""
    intensidade = min(1.0, abs(velocidade))
    if intensidade < 0.001:
        return 0.0
    return PASSOS_S_MIN + intensidade * (PASSOS_S_MAX - PASSOS_S_MIN)


class Acionamento(ABC):
    """Contrato de qualquer coisa que faça o robô se mover."""

    @abstractmethod
    def aplicar(self, velocidades: Velocidades) -> None:
        """Faz o robô andar nessas velocidades. Deve ser barato e idempotente.

        É chamado a cada volta do laço do serviço, muitas vezes com o mesmo
        valor: quem implementa não deve refazer trabalho que não mudou.
        """

    @abstractmethod
    def parar(self) -> None:
        """Para os motores e os desenergiza."""

    @abstractmethod
    def fechar(self) -> None:
        """Devolve os recursos. Deve ser idempotente."""

    def __enter__(self) -> Acionamento:
        return self

    def __exit__(self, *_excecao: object) -> None:
        self.parar()
        self.fechar()


class AcionamentoSimulado(Acionamento):
    """Não move nada; anota o que teria feito.

    Existe por dois motivos, e os dois valem o arquivo. O primeiro é o teste: a
    lógica de movimento do robô inteiro passa a ser verificável numa máquina de
    mesa. O segundo é o desenvolvimento — dá para subir o serviço de motores no
    notebook, mandar comandos pelo app e ver no log o que o robô faria, sem
    nenhum motor ligado. Antes, qualquer mudança aqui exigia o robô montado.
    """

    def __init__(self) -> None:
        self.historico: list[Velocidades] = []
        self.parou = 0
        self.fechado = False

    @property
    def atual(self) -> Velocidades:
        return self.historico[-1] if self.historico else Velocidades()

    def aplicar(self, velocidades: Velocidades) -> None:
        # Só o que muda entra no histórico: o laço chama isto vinte vezes por
        # segundo, e guardar repetição encheria a memória e escondereria a
        # sequência de comandos que interessa ler no teste.
        if velocidades != self.atual:
            self.historico.append(velocidades)
            logger.debug("simulado: esquerda=%.2f direita=%.2f", *_par(velocidades))

    def parar(self) -> None:
        self.parou += 1
        self.aplicar(Velocidades())

    def fechar(self) -> None:
        self.fechado = True


class AcionamentoStepper(Acionamento):
    """Dois motores de passo com driver TMC2209, pulso por PWM."""

    def __init__(self, *, invertido_esquerda: bool = False, invertido_direita: bool = False) -> None:
        # Importado aqui, e não no topo, para o módulo continuar carregando numa
        # máquina sem GPIO — é o que deixa `AcionamentoSimulado` e os testes
        # viverem no mesmo arquivo que o hardware.
        from gpiozero import DigitalOutputDevice, PWMOutputDevice

        self._inv_esq = invertido_esquerda
        self._inv_dir = invertido_direita

        # `initial_value=True` porque EN é active-low: o robô nasce com os
        # motores desenergizados, e não travados segurando o eixo.
        self._en_esq = DigitalOutputDevice(ESQ_EN, initial_value=True)
        self._en_dir = DigitalOutputDevice(DIR_EN, initial_value=True)
        self._dir_esq = DigitalOutputDevice(ESQ_DIR)
        self._dir_dir = DigitalOutputDevice(DIR_DIR)
        # `frequency` só passa a valer quando o duty sai de zero; começa em algo
        # válido porque frequência zero não existe para o PWM.
        self._step_esq = PWMOutputDevice(ESQ_STEP, frequency=PASSOS_S_MIN)
        self._step_dir = PWMOutputDevice(DIR_STEP, frequency=PASSOS_S_MIN)

        self._energizado = False
        self._aplicado = Velocidades()
        self._sentidos: tuple[bool | None, bool | None] = (None, None)

    def aplicar(self, velocidades: Velocidades) -> None:
        if velocidades == self._aplicado:
            return
        self._aplicado = velocidades

        if velocidades.parado:
            self.parar()
            return

        efetivas = velocidades.invertendo(self._inv_esq, self._inv_dir)
        if not self._energizado:
            self._en_esq.off()  # LOW habilita
            self._en_dir.off()
            self._energizado = True

        self._pino(self._dir_esq, self._step_esq, efetivas.esquerda, lado=0)
        self._pino(self._dir_dir, self._step_dir, efetivas.direita, lado=1)

    def _pino(self, direcao, passo, velocidade: float, *, lado: int) -> None:
        """Ajusta sentido e frequência de um lado."""
        sentido = velocidade >= 0
        if self._sentidos[lado] is not sentido:
            # O TMC2209 exige que o DIR já esteja estável quando o flanco do
            # STEP chega. Zerar o duty antes de virar o sentido garante isso sem
            # nenhum `sleep`: enquanto não há pulso, não há flanco para chegar
            # cedo demais.
            passo.value = 0.0
            direcao.value = 1 if sentido else 0
            self._sentidos = _substituir(self._sentidos, lado, sentido)

        frequencia = passos_por_segundo(velocidade)
        if frequencia <= 0:
            passo.value = 0.0
            return
        passo.frequency = frequencia
        # 50% de ciclo: o TMC2209 conta o flanco de subida, e metade do período
        # dá a margem mais folgada dos dois lados para o driver enxergá-lo.
        passo.value = 0.5

    def parar(self) -> None:
        self._step_esq.value = 0.0
        self._step_dir.value = 0.0
        self._en_esq.on()  # HIGH desabilita
        self._en_dir.on()
        self._energizado = False
        self._aplicado = Velocidades()
        self._sentidos = (None, None)

    def fechar(self) -> None:
        for dispositivo in (
            self._step_esq,
            self._step_dir,
            self._dir_esq,
            self._dir_dir,
            self._en_esq,
            self._en_dir,
        ):
            try:
                dispositivo.close()
            except Exception as exc:  # pragma: no cover - fechar não pode falhar
                logger.debug("não consegui fechar um pino: %s", exc)


def criar_acionamento(
    backend: str, *, invertido_esquerda: bool = False, invertido_direita: bool = False
) -> Acionamento:
    """Instancia o acionamento pedido.

    `"auto"` tenta o hardware e cai para o simulado quando não há GPIO. É o que
    permite subir o serviço num notebook sem editar configuração — e o log diz
    com todas as letras que o robô não vai andar, para ninguém confundir
    "simulado" com "quebrado".
    """
    escolha = backend.strip().lower()
    if escolha == "simulado":
        logger.warning("acionamento SIMULADO: os motores não vão se mexer")
        return AcionamentoSimulado()

    try:
        return AcionamentoStepper(
            invertido_esquerda=invertido_esquerda, invertido_direita=invertido_direita
        )
    except Exception as exc:
        if escolha != "auto":
            raise
        logger.warning("sem GPIO aqui (%s); seguindo em modo simulado, sem mover nada", exc)
        return AcionamentoSimulado()


def _par(velocidades: Velocidades) -> tuple[float, float]:
    return velocidades.esquerda, velocidades.direita


def _substituir(
    sentidos: tuple[bool | None, bool | None], indice: int, valor: bool
) -> tuple[bool | None, bool | None]:
    lista = list(sentidos)
    lista[indice] = valor
    return (lista[0], lista[1])
