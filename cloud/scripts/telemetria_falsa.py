"""Telemetria plausível de um robô que não existe.

Usado por dois scripts com propósitos diferentes:

- `semear-demonstracao.py` grava direto no banco. É o atalho: enche o histórico
  de horas passadas num instante, para as telas terem o que mostrar.
- `robo-falso.py` publica no MQTT, como o robô publicaria. É o caminho inteiro:
  broker → ingestor → banco → API → app. Um dado que aparece na tela do celular
  vindo dele provou cada peça da corrente.

O que os dois geram é **plausível, não aleatório**. Um gráfico de ruído branco
não teria mostrado que o eixo do tempo estava invertido; um que desce como
bateria de verdade mostrou. E a velocidade do trajeto bate com o campo
`velocidade_kmh` que ele mesmo publica — quando não batia, o mapa mostrava o
robô saltando 16 metros entre amostras de 5 s enquanto o gráfico ao lado jurava
2 km/h, e quem olhasse as duas telas juntas iria procurar o defeito na conversão
de unidade, que estava certa.
"""

from __future__ import annotations

import math
import random
from datetime import datetime, timedelta, timezone

#: Campus da Setrem, em Três de Maio (RS). O trajeto passeia em volta disto.
CENTRO_LAT = -27.7708
CENTRO_LON = -54.2406

#: Raio do passeio, em graus (~110 m). Escolhido para bater com a velocidade
#: publicada: três voltas na janela, uma amostra a cada 5 s, dá ~4 m entre
#: pontos — cerca de 3 km/h, a mesma ordem do que `velocidade_kmh` diz.
RAIO = 0.0010

#: Marca de tudo que sai daqui. `semear-demonstracao.py --limpar` apaga por
#: ela, e só por ela — nunca por janela de tempo, que levaria junto a
#: telemetria de verdade do mesmo período.
MARCA = {"demo": True}

#: Tópicos, iguais aos de `robo_common/topics.py`. Repetidos aqui porque estes
#: scripts vivem no lado da nuvem, que é deliberadamente independente da lib do
#: Pi — a interface compartilhada é o nome do tópico, e ele é estável.
TOPICOS = {
    "gps": "robo/telemetria/gps",
    "bateria": "robo/telemetria/bateria",
    "motores": "robo/telemetria/motores",
    "wifi": "robo/telemetria/wifi",
}


def posicao(indice: int, total: int) -> tuple[float, float]:
    """Um ponto do passeio em volta do campus.

    A volta é um círculo deformado por duas senoides de períodos diferentes:
    um círculo perfeito denunciaria o dado como falso na primeira olhada no
    mapa, e um caminho aleatório teria o robô se teletransportando.
    """
    angulo = 2 * math.pi * indice / max(1.0, total / 3)
    lat = CENTRO_LAT + RAIO * math.sin(angulo) * (1 + 0.25 * math.sin(angulo * 3.7))
    lon = CENTRO_LON + RAIO * math.cos(angulo) * (1 + 0.25 * math.cos(angulo * 2.3))
    return round(lat, 6), round(lon, 6)


def gps(indice: int, total: int, instante: datetime) -> dict:
    lat, lon = posicao(indice, total)
    # Um robô real para: para alguém falar com ele, para desviar, porque
    # travou. Sem paradas, o gráfico de velocidade vira uma reta.
    parado = (indice // 40) % 7 == 0
    return {
        "lat": lat,
        "lon": lon,
        "fix": True,
        "satelites": random.randint(6, 11),
        "velocidade_kmh": 0.0 if parado else round(random.uniform(0.8, 2.4), 2),
        "ts": instante.timestamp(),
        **MARCA,
    }


class Bateria:
    """Descarga contínua, com recarga quando alguém pluga.

    Guarda estado porque a carga depende de onde estava — é o que faz a linha
    do gráfico descer em vez de pular.
    """

    def __init__(self, inicial: float = 97.0) -> None:
        self.percentual = inicial

    def proxima(self, instante: datetime, passo_s: float) -> dict:
        # ~4% por hora andando; a variação é o ruído normal de um medidor.
        self.percentual -= (4.0 * passo_s / 3600) + random.uniform(-0.05, 0.05)
        if self.percentual < 22:
            self.percentual = 97.0  # alguém plugou
        percentual = round(max(0.0, min(100.0, self.percentual)), 1)
        return {
            "percentual": percentual,
            # 12 V nominais: cheia perto de 12,6, vazia perto de 11,1.
            "tensao_v": round(11.1 + 1.5 * percentual / 100, 2),
            "ts": instante.timestamp(),
            **MARCA,
        }


def motores(indice: int, instante: datetime) -> dict:
    """Comandos coerentes com um robô andando e curvando."""
    acao, esquerda, direita = [
        ("frente", 0.7, 0.7),
        ("frente", 0.9, 0.9),
        ("mover", 0.8, 0.35),  # curva à direita
        ("frente", 0.6, 0.6),
        ("parar", 0.0, 0.0),
    ][(indice // 12) % 5]
    return {
        "acao": acao,
        "velocidade": round(100 * max(esquerda, direita)),
        "esquerda": esquerda,
        "direita": direita,
        "ts": instante.timestamp(),
        **MARCA,
    }


def wifi(indice: int, instante: datetime) -> dict:
    """Estado da rede, com quedas — é o caso que o app precisa saber mostrar."""
    caiu = (indice // 30) % 11 == 0
    return {
        "conectado": not caiu,
        "ssid": "" if caiu else "Setrem",
        "ip": "" if caiu else "192.168.1.150",
        "ts": instante.timestamp(),
        **MARCA,
    }


def historico(horas: float, fim: datetime | None = None):
    """Gera as horas anteriores de uma vez, para gravar direto no banco.

    Produz `(instante, tipo, topico, payload)`, já ordenado por fonte. Cada
    grandeza tem o próprio passo: GPS a cada 5 s é o que desenha um trajeto
    contínuo, e bateria a cada 5 s seriam milhares de linhas iguais.
    """
    fim = fim or datetime.now(tz=timezone.utc)
    inicio = fim - timedelta(hours=horas)

    def instantes(passo_s: int):
        total = max(1, int(horas * 3600 / passo_s))
        for indice in range(total):
            yield indice, total, inicio + timedelta(seconds=indice * passo_s)

    for indice, total, instante in instantes(5):
        yield instante, "gps", TOPICOS["gps"], gps(indice, total, instante)

    carga = Bateria()
    for _indice, _total, instante in instantes(60):
        yield instante, "bateria", TOPICOS["bateria"], carga.proxima(instante, 60)

    for indice, _total, instante in instantes(10):
        yield instante, "motores", TOPICOS["motores"], motores(indice, instante)

    for indice, _total, instante in instantes(60):
        yield instante, "wifi", TOPICOS["wifi"], wifi(indice, instante)
