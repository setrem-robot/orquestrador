"""Vigia dos comandos de movimento — para o robô quando o controle emudece.

O app manda `F` quando o dedo desce e `S` quando o dedo sobe. Entre os dois,
nada. Isso quer dizer que qualquer falha enquanto o dedo está no botão — o
celular saindo de alcance, a bateria dele acabando, o app sendo fechado, o
Bluetooth caindo — deixa o último comando valendo. O robô fica andando, sem
ninguém no controle.

Existe `{"tipo":"parada_emergencia"}`, mas é um botão que alguém precisa
apertar: não serve para o caso em que justamente o caminho até esse botão
sumiu.

Daí este vigia. O app repete o comando enquanto o dedo está no botão, e o robô
só continua se andando enquanto essa repetição chegar. Silêncio passa a
significar "pare", que é a interpretação segura: uma parada indevida é um
susto, um robô andando sem controle é um estrago.

É lógica pura, sem GPIO e sem relógio próprio — quem chama informa o instante.
É o que permite testá-lo sem um Raspberry Pi e sem esperar em tempo real.
"""

from __future__ import annotations

#: Ações que deixam o robô em movimento. "parar" não está aqui: depois dela não
#: há o que vigiar.
ACOES_DE_MOVIMENTO = frozenset({"frente", "tras", "esquerda", "direita"})


class Vigia:
    """Diz quando faz tempo demais que o último comando de movimento chegou."""

    def __init__(self, timeoutS: float) -> None:
        #: 0 ou negativo desliga o vigia. Existe para quem ainda usa um app que
        #: não repete o comando: sem a repetição, vigiar pararia o robô no meio
        #: de todo movimento. Desligar é a escolha errada para a segurança, e
        #: por isso o padrão do serviço é ligado.
        self.timeoutS = timeoutS
        self.ultimoMovimentoEm: float | None = None

    @property
    def ligado(self) -> bool:
        return self.timeoutS > 0

    @property
    def vigiando(self) -> bool:
        """Se há um movimento em curso sendo vigiado agora."""
        return self.ultimoMovimentoEm is not None

    def comandoRecebido(self, acao: str, agora: float) -> None:
        """Registra um comando. Movimento arma o vigia; parada desarma."""
        if acao in ACOES_DE_MOVIMENTO:
            self.movimentoRecebido(agora)
        else:
            self.paradaRecebida()

    def movimentoRecebido(self, agora: float) -> None:
        """Arma o vigia: há robô andando, e isso precisa continuar sendo pedido.

        Existe ao lado de `comando_recebido` porque nem todo comando diz pelo
        nome se move ou não: `{"acao":"mover"}` com os dois eixos em zero é uma
        parada, e com eles fora de zero é movimento. Quem já converteu o comando
        em velocidade sabe a resposta; o nome da ação, sozinho, não.
        """
        self.ultimoMovimentoEm = agora

    def paradaRecebida(self) -> None:
        """Desarma o vigia: não há movimento a vigiar."""
        self.ultimoMovimentoEm = None

    def expirou(self, agora: float) -> bool:
        """Se o robô deve ser parado por falta de notícias.

        Responde uma vez só por silêncio: quem chama vai mandar parar, e mandar
        parar de novo a cada volta do laço encheria o log e o tópico de status
        com a mesma parada repetida.
        """
        if not self.ligado or self.ultimoMovimentoEm is None:
            return False
        if agora - self.ultimoMovimentoEm < self.timeoutS:
            return False
        self.ultimoMovimentoEm = None
        return True
