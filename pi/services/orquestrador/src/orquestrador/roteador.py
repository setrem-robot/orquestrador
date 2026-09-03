"""Roteamento puro de comandos do app -> tópicos de domínio.

Este módulo NÃO conhece MQTT: ele só transforma um comando (dict já
desserializado) numa lista de publicações (tópico, payload). Isso o torna
trivial de testar e mantém a lógica de decisão separada da infraestrutura.

Formatos de entrada aceitos (Bluetooth -> ESP32 -> serial_ingestor):

  Formato compacto (app atual):
    {"cmd": "F"}   frente     {"cmd": "B"}   trás
    {"cmd": "L"}   esquerda   {"cmd": "R"}   direita
    {"cmd": "S"}   parar

  Formato expandido (retrocompatível):
    {"tipo": "motor",  "acao": "frente", "velocidade": 80}
    {"tipo": "motor",  "acao": "mover", "linear": 0.8, "angular": -0.3}
    {"tipo": "voz",    "texto": "olá, tudo bem?"}
    {"tipo": "parada_emergencia"}

  Rota segura (planejada no app, entregue fatiada — ver ComandoRota):
    {"tipo": "rota", "acao": "inicio", "total": 4, "nome": "volta-quadra"}
    {"tipo": "rota", "acao": "ponto", "i": 0, "lat": -28.12, "lon": -54.12}
    {"tipo": "rota", "acao": "fim"}

Veja docs/contrato-mqtt.md para a especificação completa.

Cada tipo de comando é uma subclasse de [ComandoRoteavel] (Command Pattern):
adicionar um comando novo é criar a subclasse e registrar uma instância em
`_ROTAS` — `rotear()`, no fim do módulo, nunca muda.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

from robo_common import topics

logger = logging.getLogger(__name__)

# Uma publicação resultante do roteamento: para onde e o quê.
Publicacao = tuple[str, dict[str, Any]]

# Velocidade padrão quando o app manda uma direção sem informar intensidade.
VELOCIDADE_PADRAO = 60

# Formato compacto do app: letra única -> ação de motor.
_CMD_PARA_ACAO: dict[str, str] = {
    "F": "frente",
    "B": "tras",
    "L": "esquerda",
    "R": "direita",
    "S": "parar",
}


class ComandoRoteavel(ABC):
    """Traduz o dict de um comando em publicações MQTT (tópico, payload).
    Cada subclasse cuida de um valor de `comando["tipo"]`.
    """

    @abstractmethod
    def rotear(self, cmd: dict[str, Any]) -> list[Publicacao]:
        """Traduz o comando (ainda com o campo "tipo") em publicações.

        Nunca levanta exceção: comando malformado -> lista vazia + log.
        """


class ComandoMotor(ComandoRoteavel):
    """tipo: "motor" — movimenta o robô.

    Duas formas convivem. As quatro direções são o que o app manda hoje, com um
    botão por direção. `"mover"` é a forma contínua: `linear` e `angular`, cada
    um de -1 a 1, que é o que um joystick ou um controle analógico produz — e o
    que permite curvar andando, em vez de escolher entre andar e girar.

    O serviço de motores trata as duas do mesmo jeito (ver `cinematica.py`);
    aqui elas são apenas validadas antes de sair do mundo não-confiável do app.
    """

    # Ações de motor que aceitamos do app. Mantém o robô previsível: qualquer
    # outra ação é rejeitada antes de chegar ao grupo de Movimento.
    ACOES_VALIDAS = {"frente", "tras", "esquerda", "direita", "parar", "mover"}

    def rotear(self, cmd: dict[str, Any]) -> list[Publicacao]:
        acao = cmd.get("acao")
        if acao not in self.ACOES_VALIDAS:
            logger.warning("Comando de motor com ação inválida: %r", acao)
            return []

        if acao == "mover":
            return [
                (
                    topics.MOTORES_COMANDO,
                    {
                        "acao": "mover",
                        "linear": self._limitar_eixo(cmd.get("linear")),
                        "angular": self._limitar_eixo(cmd.get("angular")),
                    },
                )
            ]

        velocidade = 0 if acao == "parar" else self._limitar_velocidade(cmd.get("velocidade"))
        return [(topics.MOTORES_COMANDO, {"acao": acao, "velocidade": velocidade})]

    @staticmethod
    def _limitar_velocidade(valor: Any) -> int:
        """Converte e satura a velocidade em [0, 100].

        O app é não-confiável: pode mandar string, número fora da faixa ou nada.
        """
        try:
            v = int(valor)
        except (TypeError, ValueError):
            return VELOCIDADE_PADRAO
        return max(0, min(100, v))

    @staticmethod
    def _limitar_eixo(valor: Any) -> float:
        """Converte e satura um eixo em [-1, 1].

        Ausente vira zero, e não um padrão de movimento: um `mover` sem eixo
        nenhum é uma parada, e essa é a interpretação segura de um comando pela
        metade.
        """
        try:
            v = float(valor)
        except (TypeError, ValueError):
            return 0.0
        return max(-1.0, min(1.0, v))


class ComandoVoz(ComandoRoteavel):
    """tipo: "voz" — pede pro grupo de IA falar um texto."""

    def rotear(self, cmd: dict[str, Any]) -> list[Publicacao]:
        texto = cmd.get("texto")
        if not isinstance(texto, str) or not texto.strip():
            logger.warning("Comando de voz sem texto válido: %r", cmd)
            return []
        return [(topics.VOZ_FALAR, {"texto": texto.strip()})]


class ComandoParadaEmergencia(ComandoRoteavel):
    """tipo: "parada_emergencia" — zera os motores imediatamente."""

    def rotear(self, _cmd: dict[str, Any]) -> list[Publicacao]:
        # Segurança em primeiro lugar: zera os motores imediatamente.
        return [(topics.MOTORES_COMANDO, {"acao": "parar", "velocidade": 0})]


class ComandoRota(ComandoRoteavel):
    """tipo: "rota" — a rota segura planejada no app, entregue fatiada.

    A rota chega em mensagens separadas, não numa só: cada linha BLE é limitada
    a 512 bytes pelo firmware do ESP32, e uma rota com muitos pontos não caberia.
    Então o app manda `inicio` (quantos pontos vêm), um `ponto` por waypoint e
    `fim`. Este roteador é sem estado — valida cada mensagem isoladamente e a
    republica em `robo/rota/comando`; quem remonta a rota inteira é o consumidor
    (hoje ninguém; é o gancho para um futuro serviço de navegação).

    Como a origem é o app (não-confiável), coordenadas fora da faixa geográfica
    ou índices malformados são descartados aqui, antes de virarem rota.
    """

    ACOES_VALIDAS = {"inicio", "ponto", "fim"}

    def rotear(self, cmd: dict[str, Any]) -> list[Publicacao]:
        acao = cmd.get("acao")
        if acao not in self.ACOES_VALIDAS:
            logger.warning("Comando de rota com ação inválida: %r", acao)
            return []

        if acao == "inicio":
            total = self._inteiro_nao_negativo(cmd.get("total"))
            if total is None:
                logger.warning("Rota 'inicio' sem total válido: %r", cmd)
                return []
            payload: dict[str, Any] = {"acao": "inicio", "total": total}
            nome = cmd.get("nome")
            if isinstance(nome, str) and nome.strip():
                payload["nome"] = nome.strip()
            return [(topics.ROTA_COMANDO, payload)]

        if acao == "fim":
            return [(topics.ROTA_COMANDO, {"acao": "fim"})]

        # acao == "ponto"
        indice = self._inteiro_nao_negativo(cmd.get("i"))
        lat = self._coordenada(cmd.get("lat"), limite=90.0)
        lon = self._coordenada(cmd.get("lon"), limite=180.0)
        if indice is None or lat is None or lon is None:
            logger.warning("Ponto de rota inválido (i/lat/lon): %r", cmd)
            return []
        return [(topics.ROTA_COMANDO, {"acao": "ponto", "i": indice, "lat": lat, "lon": lon})]

    @staticmethod
    def _inteiro_nao_negativo(valor: Any) -> int | None:
        try:
            n = int(valor)
        except (TypeError, ValueError):
            return None
        return n if n >= 0 else None

    @staticmethod
    def _coordenada(valor: Any, *, limite: float) -> float | None:
        """Número dentro de [-limite, limite], ou None. Fora da faixa não é
        um ponto no planeta — é lixo de transmissão ou app com defeito."""
        try:
            v = float(valor)
        except (TypeError, ValueError):
            return None
        return v if -limite <= v <= limite else None


class ComandoWifi(ComandoRoteavel):
    """tipo: "wifi" — repassa provisionamento de Wi-Fi ao serviço wifi."""

    CAMPOS_REPASSADOS = ("ssid", "senha", "password")

    def rotear(self, cmd: dict[str, Any]) -> list[Publicacao]:
        # A credencial chegou pelo mesmo caminho dos comandos (app -> ESP32 ->
        # serial), pois o Pi não fala Bluetooth.
        payload: dict[str, Any] = {"acao": cmd.get("acao", "conectar")}
        for campo in self.CAMPOS_REPASSADOS:
            if campo in cmd:
                payload[campo] = cmd[campo]
        return [(topics.WIFI_COMANDO, payload)]


# Tabela de despacho: "tipo" do comando -> instância que sabe roteá-lo.
# Adicionar um novo tipo de comando é: criar a subclasse de ComandoRoteavel
# acima e registrar uma instância aqui. `rotear()`, embaixo, não muda.
_ROTAS: dict[str, ComandoRoteavel] = {
    "motor": ComandoMotor(),
    "voz": ComandoVoz(),
    "parada_emergencia": ComandoParadaEmergencia(),
    "wifi": ComandoWifi(),
    "rota": ComandoRota(),
}


def rotear(comando: dict[str, Any]) -> list[Publicacao]:
    """Traduz um comando do app numa lista de publicações (tópico, payload).

    Retorna lista vazia para comandos desconhecidos ou malformados — nunca
    levanta exceção, porque a entrada vem de um cliente externo não-confiável.
    """
    if not isinstance(comando, dict):
        logger.warning("Comando ignorado (não é objeto JSON): %r", comando)
        return []

    # Formato compacto {"cmd": "F"}: normaliza para ação de motor antes de rotear.
    cmd = comando.get("cmd")
    if cmd is not None:
        acao = _CMD_PARA_ACAO.get(str(cmd).upper())
        if acao is None:
            logger.warning("Comando compacto desconhecido: %r", cmd)
            return []
        return _ROTAS["motor"].rotear({"acao": acao, "velocidade": VELOCIDADE_PADRAO})

    tipo = comando.get("tipo")
    # `isinstance` antes do `get`: um "tipo" que veio como lista ou dicionário
    # não é hashável, e `_ROTAS.get(...)` levanta `TypeError` em vez de devolver
    # `None`. A promessa deste módulo — "nunca levanta exceção, porque a entrada
    # vem de um cliente externo não-confiável" — cai justamente no comando mais
    # malformado de todos.
    rota = _ROTAS.get(tipo) if isinstance(tipo, str) else None
    if rota is None:
        logger.warning("Comando de tipo desconhecido: %r", tipo)
        return []

    # Despacho polimórfico: cada `rota` é uma subclasse diferente de
    # ComandoRoteavel, mas esta linha não sabe (nem precisa saber) qual.
    return rota.rotear(comando)
