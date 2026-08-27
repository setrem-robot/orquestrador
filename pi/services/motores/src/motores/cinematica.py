"""Do comando que chega ao que cada roda deve fazer.

É lógica pura: sem GPIO, sem relógio próprio, sem MQTT. Quem chama informa o
instante. É o que permite testar o movimento do robô inteiro numa máquina de
mesa, sem Raspberry Pi e sem esperar em tempo real — o mesmo desenho de
`vigia.py`, pelo mesmo motivo.

## Por que velocidade por roda, e não quatro direções

O app manda `frente`, `tras`, `esquerda`, `direita`. São quatro casos, e cada
coisa nova que o robô precisar fazer vira um quinto: curvar andando, girar
devagar para se alinhar, seguir uma linha, obedecer a um joystick analógico.
Com quatro casos, cada um desses é um `if` novo espalhado por um arquivo que
também fala com o GPIO.

Aqui o comando vira sempre a mesma coisa — a velocidade de cada lado, entre -1
e 1 — e as quatro direções passam a ser quatro atalhos para isso. Um joystick
que mande `linear` e `angular` já funciona sem nenhuma linha nova; um terceiro
motor, ou encoders, entram numa camada que já sabe raciocinar em velocidade.

## Por que a rampa

Um motor de passo com carga não parte direto na velocidade final: ele **perde
passo** — o eixo não acompanha o pulso, o robô anda menos do que mandaram e o
driver esquenta. A rampa sobe a velocidade em um tempo curto, e o mesmo vale
para a freada: parar de um golpe é o que faz o robô dar um tranco.

A rampa não atrasa a parada de emergência. `parar_agora()` existe justamente
para o caso em que a resposta certa é a brusca — e o `vigia` a usa.
"""

from __future__ import annotations

from dataclasses import dataclass

#: O vocabulário que o app fala hoje, e o par (linear, angular) de cada palavra.
#: Girar no lugar é linear 0 com angular 1 — as rodas em sentidos opostos.
_ATALHOS: dict[str, tuple[float, float]] = {
    "frente": (1.0, 0.0),
    "tras": (-1.0, 0.0),
    "esquerda": (0.0, -1.0),
    "direita": (0.0, 1.0),
    "parar": (0.0, 0.0),
}

ACOES_VALIDAS = frozenset(_ATALHOS) | {"mover"}


@dataclass(frozen=True, slots=True)
class Velocidades:
    """O que cada lado do robô deve fazer, entre -1 (ré) e 1 (à frente)."""

    esquerda: float = 0.0
    direita: float = 0.0

    @property
    def parado(self) -> bool:
        # Um milésimo: abaixo disso o motor não gira de verdade, só assobia.
        return abs(self.esquerda) < 0.001 and abs(self.direita) < 0.001

    def invertendo(self, esquerda: bool, direita: bool) -> Velocidades:
        """Espelha os lados que foram montados ao contrário.

        Os dois motores ficam de frente um para o outro no chassi, então um
        deles gira ao contrário do outro para o robô andar reto. Qual é qual
        depende de como foi parafusado — daí ser configuração, e não constante.
        """
        return Velocidades(
            esquerda=-self.esquerda if esquerda else self.esquerda,
            direita=-self.direita if direita else self.direita,
        )


def de_eixos(linear: float, angular: float) -> Velocidades:
    """Modelo diferencial: avanço e giro viram velocidade de cada lado.

    `linear` positivo vai para a frente, `angular` positivo gira para a direita.

    Somar os dois pode passar de 1 — meia-força à frente com giro cheio daria
    1,5 de um lado. Em vez de cortar no teto, o que distorceria a curva pedida
    (o lado saturado pararia de crescer e o robô curvaria mais do que se pediu),
    os dois lados são divididos pelo maior. A trajetória fica a mesma, só mais
    devagar — que é o que alguém segurando o controle espera.
    """
    esquerda = linear + angular
    direita = linear - angular

    excesso = max(abs(esquerda), abs(direita))
    if excesso > 1.0:
        esquerda /= excesso
        direita /= excesso

    return Velocidades(esquerda=esquerda, direita=direita)


def do_comando(acao: str, velocidade: int = 60, *, linear: float = 0.0, angular: float = 0.0) -> Velocidades:
    """Traduz um comando do contrato MQTT para velocidade de cada lado.

    `acao` é uma das quatro direções de sempre (usando `velocidade`, de 0 a
    100), ou `"mover"` — a forma contínua, que usa `linear` e `angular`.
    """
    if acao == "mover":
        return de_eixos(_limitar(linear), _limitar(angular))

    atalho = _ATALHOS.get(acao)
    if atalho is None:
        raise ValueError(f"ação de motor desconhecida: {acao!r}")

    escala = max(0.0, min(100.0, float(velocidade))) / 100.0
    eixo_linear, eixo_angular = atalho
    return de_eixos(eixo_linear * escala, eixo_angular * escala)


def _limitar(valor: float) -> float:
    return max(-1.0, min(1.0, float(valor)))


class Rampa:
    """Aproxima a velocidade atual da pedida, sem saltos.

    Guarda estado — a velocidade em que o robô está — mas não guarda relógio:
    cada `avancar` recebe quanto tempo passou. Testar uma aceleração de meio
    segundo não custa meio segundo.
    """

    def __init__(self, por_segundo: float) -> None:
        #: Quanto a velocidade de um lado pode variar em um segundo, na escala
        #: de -1 a 1. 3,0 leva do parado ao máximo em um terço de segundo. Zero
        #: ou negativo desliga a rampa e faz cada comando valer imediatamente —
        #: que é o comportamento antigo, mantido para quem tiver um chassi leve
        #: o bastante para não perder passo.
        self._por_segundo = por_segundo
        self._atual = Velocidades()
        self._alvo = Velocidades()

    @property
    def ligada(self) -> bool:
        return self._por_segundo > 0

    @property
    def atual(self) -> Velocidades:
        return self._atual

    @property
    def alvo(self) -> Velocidades:
        return self._alvo

    @property
    def alcancou(self) -> bool:
        """Se já não há o que acelerar — o laço pode dormir mais."""
        return self._atual == self._alvo

    def pedir(self, alvo: Velocidades) -> None:
        """Registra para onde ir. A chegada acontece nos `avancar` seguintes."""
        self._alvo = alvo
        if not self.ligada:
            self._atual = alvo

    def parar_agora(self) -> Velocidades:
        """Zera tudo sem rampa.

        Para a parada de emergência e para o vigia: quando o motivo de parar é
        que ninguém está no controle, meio segundo de desaceleração é meio
        segundo de robô andando sozinho.
        """
        self._alvo = Velocidades()
        self._atual = Velocidades()
        return self._atual

    def avancar(self, dt: float) -> Velocidades:
        """Caminha `dt` segundos na direção do alvo e devolve onde ficou."""
        if not self.ligada or self._atual == self._alvo:
            self._atual = self._alvo
            return self._atual

        passo = self._por_segundo * max(0.0, dt)
        self._atual = Velocidades(
            esquerda=_aproximar(self._atual.esquerda, self._alvo.esquerda, passo),
            direita=_aproximar(self._atual.direita, self._alvo.direita, passo),
        )
        return self._atual


def _aproximar(de: float, para: float, passo: float) -> float:
    """Move `de` na direção de `para`, no máximo `passo`, sem passar do ponto."""
    diferenca = para - de
    if abs(diferenca) <= passo:
        return para
    return de + passo * (1.0 if diferenca > 0 else -1.0)
