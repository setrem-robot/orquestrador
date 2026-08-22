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
    {"tipo": "voz",    "texto": "olá, tudo bem?"}
    {"tipo": "parada_emergencia"}

Veja docs/contrato-mqtt.md para a especificação completa.

## Por que uma classe por tipo de comando

Cada tipo de comando ("motor", "voz", "wifi", ...) é uma subclasse de
[ComandoRoteavel], não uma função solta. Isso não é só estética:
`rotear()` (a função no fim deste módulo) despacha para
`_ROTAS[tipo].rotear(comando)` sem nunca saber QUAL subclasse está do outro
lado — é despacho polimórfico de verdade. Adicionar um comando novo é criar
uma subclasse e registrar uma instância em `_ROTAS`; `rotear()` nunca muda.
Isso é o Command Pattern: cada comando encapsula sua própria regra de
validação e tradução, atrás da mesma interface.
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
    """Uma família de comando do app: sabe traduzir o dict recebido em
    publicações MQTT (tópico, payload).

    Cada subclasse cuida de UM valor de `comando["tipo"]`. Nenhum código fora
    deste módulo instancia ou conhece as subclasses — elas só existem
    registradas em `_ROTAS`, atrás desta interface comum.
    """

    @abstractmethod
    def rotear(self, cmd: dict[str, Any]) -> list[Publicacao]:
        """Traduz o comando (ainda com o campo "tipo") em publicações.

        Nunca levanta exceção: comando malformado -> lista vazia + log.
        """


class ComandoMotor(ComandoRoteavel):
    """tipo: "motor" — movimenta o robô."""

    # Ações de motor que aceitamos do app. Mantém o robô previsível: qualquer
    # outra ação é rejeitada antes de chegar ao grupo de Movimento.
    ACOES_VALIDAS = {"frente", "tras", "esquerda", "direita", "parar"}

    def rotear(self, cmd: dict[str, Any]) -> list[Publicacao]:
        acao = cmd.get("acao")
        if acao not in self.ACOES_VALIDAS:
            logger.warning("Comando de motor com ação inválida: %r", acao)
            return []

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
    rota = _ROTAS.get(tipo)
    if rota is None:
        logger.warning("Comando de tipo desconhecido: %r", tipo)
        return []

    # Despacho polimórfico: cada `rota` é uma subclasse diferente de
    # ComandoRoteavel, mas esta linha não sabe (nem precisa saber) qual.
    return rota.rotear(comando)
