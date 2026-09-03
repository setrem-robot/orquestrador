"""Quem pode perguntar, e com que frequência.

Duas portas, e a diferença entre elas é deliberada:

**A do app** exige `Authorization: Bearer <token>`. O token vale para todo o
histórico, inclusive as coordenadas exatas do robô.

**A da landing page** não exige nada, porque não pode: uma página estática que
consulta a API carrega a credencial no JavaScript, à vista de qualquer um que
abra o inspetor. Um token ali seria um token público com nome de segredo. Em
vez de fingir, as rotas públicas servem menos: só o resumo e a posição com a
precisão reduzida (ver `PRECISAO_GPS_PUBLICA`).
"""

from __future__ import annotations

import hmac
import logging
import os
import time
from collections import defaultdict, deque

logger = logging.getLogger("api.seguranca")

TOKEN = os.environ.get("API_TOKEN", "").strip()

#: Casas decimais das coordenadas servidas sem token. Quatro dão ~11 metros:
#: o suficiente para ver o robô andando pelo campus no mapa, e não o
#: suficiente para saber em que sala ele está. Zero desliga o arredondamento.
PRECISAO_GPS_PUBLICA = int(os.environ.get("PRECISAO_GPS_PUBLICA", "4"))

#: As rotas públicas podem ser desligadas por inteiro — útil enquanto a landing
#: page não existir, ou se o robô for para um lugar onde a posição não deva
#: aparecer.
PUBLICO_HABILITADO = os.environ.get("PUBLICO_HABILITADO", "true").lower() in {
    "1",
    "true",
    "yes",
    "on",
}

#: Se dá para acreditar no `CF-Connecting-IP` / `X-Forwarded-For` que chegam.
#:
#: Atrás do túnel da Cloudflare o IP da conexão é sempre o do `cloudflared`, e
#: limitar por ele limitaria todo mundo junto — por isso o padrão é confiar. Mas
#: cabeçalho é texto que o cliente escreve: **quem alcançar a API por fora do
#: túnel escolhe a própria identidade e o limitador deixa de existir**, porque
#: cada requisição inventa um IP novo. Só o túnel alcança esta API, e é isso que
#: torna o padrão aceitável; publicando a porta de outro jeito, desligue aqui.
CONFIAR_EM_PROXY = os.environ.get("CONFIAR_EM_PROXY", "true").lower() in {
    "1",
    "true",
    "yes",
    "on",
}

#: Por quantos segundos guardar a resposta do resumo público.
#:
#: `resumo()` conta a tabela inteira, não tem recorte de tempo e é servido **sem
#: token**: quanto mais histórico o robô junta, mais cara ela fica, e qualquer
#: um pode pedi-la. Guardar a resposta por um minuto faz o custo parar de
#: depender de quantas pessoas abriram a página — a landing atualiza a cada 30 s,
#: então ninguém percebe a diferença.
CACHE_RESUMO_S = float(os.environ.get("CACHE_RESUMO_S", "60"))


class CacheCurto:
    """Guarda um valor por alguns segundos. Um só, e sem thread de limpeza."""

    def __init__(self, validade_s: float) -> None:
        self._validade = validade_s
        self._valor: object | None = None
        self._gravado_em = 0.0

    def obter(self):
        if self._valor is None or time.monotonic() - self._gravado_em >= self._validade:
            return None
        return self._valor

    def guardar(self, valor) -> None:
        self._valor = valor
        self._gravado_em = time.monotonic()


def token_configurado() -> bool:
    return bool(TOKEN)


def token_confere(cabecalho: str | None) -> bool:
    """Valida o `Authorization: Bearer <token>`.

    `compare_digest` em vez de `==` porque a comparação normal de strings
    devolve `False` no primeiro caractere diferente, e o tempo disso vaza
    quantos caracteres estavam certos. É pouco explorável numa rede real e
    custa uma linha evitar.
    """
    if not TOKEN:
        # Sem token configurado a API não abre a porta do app: é melhor
        # recusar todo mundo do que servir o histórico inteiro a quem
        # descobrir o endereço. `/saude` continua respondendo, e diz isto.
        return False
    if not cabecalho or not cabecalho.startswith("Bearer "):
        return False
    return hmac.compare_digest(cabecalho[7:].strip(), TOKEN)


def arredondar_coordenada(valor: float | None) -> float | None:
    if valor is None or PRECISAO_GPS_PUBLICA <= 0:
        return valor
    return round(valor, PRECISAO_GPS_PUBLICA)


class Limitador:
    """Teto de requisições por IP, numa janela deslizante.

    Em memória de propósito: são poucos clientes, e trazer Redis para isto
    seria mais infraestrutura do que proteção. O que ele evita não é ataque —
    é a página aberta em três abas com um `setInterval` de meio segundo
    derrubando a VM sozinha.
    """

    def __init__(self, teto: int, janela_s: float) -> None:
        self._teto = teto
        self._janela = janela_s
        self._batidas: dict[str, deque[float]] = defaultdict(deque)

    def permitir(self, chave: str) -> bool:
        agora = time.monotonic()
        fila = self._batidas[chave]
        # `>=` e não `>`: uma batida com exatamente a idade da janela já saiu
        # dela. Com `>`, duas chamadas que o relógio não consegue separar (o
        # `time.monotonic()` do Windows anda de 15 em 15 ms) contavam como
        # simultâneas para sempre, e a janela nunca expirava.
        while fila and agora - fila[0] >= self._janela:
            fila.popleft()
        if len(fila) >= self._teto:
            return False
        fila.append(agora)
        # Sem isto o dicionário cresceria para sempre num serviço exposto à
        # internet: cada IP que passa uma vez deixaria uma fila vazia atrás.
        if len(self._batidas) > 5000:
            self._esquecer_ociosos(agora)
        return True

    def _esquecer_ociosos(self, agora: float) -> None:
        mortos = [
            chave
            for chave, fila in self._batidas.items()
            if not fila or agora - fila[-1] > self._janela * 10
        ]
        for chave in mortos:
            del self._batidas[chave]
        logger.debug("limitador esqueceu %d clientes ociosos", len(mortos))
