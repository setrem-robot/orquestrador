"""Vigia dos comandos de movimento — para o robô quando o controle emudece.

O app manda `F` quando o dedo desce e `S` quando o dedo sobe. Entre os dois,
nada. Isso quer dizer que qualquer falha enquanto o dedo está no botão — o
celular saindo de alcance, a bateria dele acabando, o app sendo fechado, o ESP32
travando, o cabo serial soltando — deixa o último comando valendo. O robô fica
andando, sem ninguém no controle.

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

    def __init__(self, timeout_s: float) -> None:
        #: 0 ou negativo desliga o vigia. Existe para quem ainda usa um app que
        #: não repete o comando: sem a repetição, vigiar pararia o robô no meio
        #: de todo movimento. Desligar é a escolha errada para a segurança, e
        #: por isso o padrão do serviço é ligado.
        self._timeout_s = timeout_s
        self._ultimo_movimento_em: float | None = None

    @property
    def ligado(self) -> bool:
        return self._timeout_s > 0

    @property
    def vigiando(self) -> bool:
        """Se há um movimento em curso sendo vigiado agora."""
        return self._ultimo_movimento_em is not None

    def comando_recebido(self, acao: str, agora: float) -> None:
        """Registra um comando. Movimento arma o vigia; parada desarma."""
        if acao in ACOES_DE_MOVIMENTO:
            self._ultimo_movimento_em = agora
        else:
            self._ultimo_movimento_em = None

    def expirou(self, agora: float) -> bool:
        """Se o robô deve ser parado por falta de notícias.

        Responde uma vez só por silêncio: quem chama vai mandar parar, e mandar
        parar de novo a cada volta do laço encheria o log e o tópico de status
        com a mesma parada repetida.
        """
        if not self.ligado or self._ultimo_movimento_em is None:
            return False
        if agora - self._ultimo_movimento_em < self._timeout_s:
            return False
        self._ultimo_movimento_em = None
        return True
