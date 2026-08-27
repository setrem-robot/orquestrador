"""Enche o banco com telemetria plausível, para as telas existirem antes do robô.

O GPS ainda não está montado, e a bateria ainda não é publicada por ninguém.
Sem dados, o app e a landing page mostram tela vazia — e uma tela vazia não diz
se o mapa está errado, se a API está errada, ou se simplesmente não há o que
mostrar. Este script separa essas três coisas.

O que ele gera é **plausível, não aleatório**: um trajeto que anda em volta do
campus da Setrem com velocidade coerente, uma bateria que só cai (e recarrega
quando alguém pluga), e comandos de motor que combinam com a curva que o
trajeto está fazendo. Um gráfico de ruído branco não teria mostrado que o eixo
do tempo estava invertido; um que desce como bateria de verdade mostrou.

Uso, na VM:

    python3 cloud/scripts/semear-demonstracao.py --horas 6
    python3 cloud/scripts/semear-demonstracao.py --limpar     # tira tudo

Ou de qualquer lugar, apontando para o banco:

    PGHOST=127.0.0.1 PGPASSWORD=... python3 cloud/scripts/semear-demonstracao.py

Os registros de demonstração são marcados com `"demo": true` no payload — é o
que torna `--limpar` seguro: ele nunca apaga telemetria de verdade.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
from datetime import datetime, timedelta, timezone

import psycopg

#: Campus da Setrem, em Três de Maio (RS). O trajeto passeia em volta disto.
CENTRO_LAT = -27.7708
CENTRO_LON = -54.2406

#: Raio do passeio, em graus (~110 m). Escolhido para bater com a velocidade
#: publicada: três voltas na janela, uma amostra a cada 5 s, dá ~4 m entre
#: pontos — cerca de 3 km/h, a mesma ordem do que o campo `velocidade_kmh` diz.
#:
#: Isso não é preciosismo. Com um raio maior o mapa mostrava o robô saltando 16
#: metros entre amostras enquanto o gráfico ao lado jurava 2 km/h, e a primeira
#: pessoa a olhar as duas telas juntas ia procurar o bug na conversão de
#: unidade — que estaria certa.
RAIO = 0.0010

#: Marca de tudo que este script grava. `--limpar` apaga por ela, e só por ela.
MARCA = {"demo": True}


def conexao() -> psycopg.Connection:
    conninfo = (
        f"host={os.environ.get('PGHOST', '127.0.0.1')} "
        f"port={os.environ.get('PGPORT', '5432')} "
        f"user={os.environ.get('PGUSER', 'robo')} "
        f"password={os.environ.get('PGPASSWORD', '')} "
        f"dbname={os.environ.get('PGDATABASE', 'robo')}"
    )
    return psycopg.connect(conninfo, autocommit=True)


def trajeto(inicio: datetime, horas: float, passo_s: int):
    """Um passeio em volta do campus, com paradas.

    A volta é um círculo deformado por duas senoides — um círculo perfeito
    denunciaria o dado como falso na primeira olhada no mapa, e um caminho
    aleatório teria o robô se teletransportando entre pontos.
    """
    total = int(horas * 3600 / passo_s)
    for indice in range(total):
        instante = inicio + timedelta(seconds=indice * passo_s)
        angulo = 2 * math.pi * indice / max(1, total / 3)

        # As deformações têm períodos diferentes do círculo, então o caminho
        # não se fecha exatamente igual a cada volta.
        lat = CENTRO_LAT + RAIO * math.sin(angulo) * (1 + 0.25 * math.sin(angulo * 3.7))
        lon = CENTRO_LON + RAIO * math.cos(angulo) * (1 + 0.25 * math.cos(angulo * 2.3))

        # Um robô real para: para alguém falar com ele, para desviar, porque
        # travou. Sem paradas, o gráfico de velocidade vira uma reta.
        parado = (indice // 40) % 7 == 0
        velocidade = 0.0 if parado else round(random.uniform(0.8, 2.4), 2)

        yield instante, "gps", "robo/telemetria/gps", {
            "lat": round(lat, 6),
            "lon": round(lon, 6),
            "fix": True,
            "satelites": random.randint(6, 11),
            "velocidade_kmh": velocidade,
            "ts": instante.timestamp(),
            **MARCA,
        }


def bateria(inicio: datetime, horas: float, passo_s: int):
    """Descarga com uma recarga no meio, se a janela for longa o bastante."""
    total = int(horas * 3600 / passo_s)
    percentual = 97.0
    for indice in range(total):
        instante = inicio + timedelta(seconds=indice * passo_s)
        # ~4% por hora andando; a variação é o ruído normal de um medidor.
        percentual -= (4.0 * passo_s / 3600) + random.uniform(-0.05, 0.05)
        if percentual < 22:
            percentual = 97.0  # alguém plugou
        yield instante, "bateria", "robo/telemetria/bateria", {
            "percentual": round(max(0.0, min(100.0, percentual)), 1),
            # 12 V nominais: cheia perto de 12,6, vazia perto de 11,1.
            "tensao_v": round(11.1 + 1.5 * percentual / 100, 2),
            "ts": instante.timestamp(),
            **MARCA,
        }


def motores(inicio: datetime, horas: float, passo_s: int):
    """Comandos coerentes com um robô andando e curvando."""
    total = int(horas * 3600 / passo_s)
    for indice in range(total):
        instante = inicio + timedelta(seconds=indice * passo_s)
        fase = (indice // 12) % 5
        acao, esquerda, direita = [
            ("frente", 0.7, 0.7),
            ("frente", 0.9, 0.9),
            ("mover", 0.8, 0.35),  # curva à direita
            ("frente", 0.6, 0.6),
            ("parar", 0.0, 0.0),
        ][fase]
        yield instante, "motores", "robo/telemetria/motores", {
            "acao": acao,
            "velocidade": round(100 * max(esquerda, direita)),
            "esquerda": esquerda,
            "direita": direita,
            "ts": instante.timestamp(),
            **MARCA,
        }


def wifi(inicio: datetime, horas: float, passo_s: int):
    """Estado da rede, com uma queda no meio — é o caso que o app precisa mostrar."""
    total = int(horas * 3600 / passo_s)
    for indice in range(total):
        instante = inicio + timedelta(seconds=indice * passo_s)
        caiu = (indice // 30) % 11 == 0
        yield instante, "wifi", "robo/telemetria/wifi", {
            "conectado": not caiu,
            "ssid": "" if caiu else "Setrem",
            "ip": "" if caiu else "192.168.1.150",
            "ts": instante.timestamp(),
            **MARCA,
        }


def semear(horas: float) -> int:
    fim = datetime.now(tz=timezone.utc)
    inicio = fim - timedelta(hours=horas)

    fontes = [
        trajeto(inicio, horas, passo_s=5),
        bateria(inicio, horas, passo_s=60),
        motores(inicio, horas, passo_s=10),
        wifi(inicio, horas, passo_s=60),
    ]

    registros = [
        (ts, tipo, topico, json.dumps(payload))
        for fonte in fontes
        for ts, tipo, topico, payload in fonte
    ]

    with conexao() as conn, conn.cursor() as cur:
        # `executemany` numa transação só: linha a linha, seis horas de dados
        # levariam minutos contra um banco remoto.
        cur.executemany(
            "INSERT INTO telemetria (ts, tipo, topico, payload) VALUES (%s, %s, %s, %s)",
            registros,
        )
    return len(registros)


def limpar() -> int:
    """Apaga só o que este script gravou.

    O filtro é o campo `demo` no payload, e não a janela de tempo: apagar por
    tempo removeria junto a telemetria de verdade que estivesse no mesmo
    período — e num robô que já rodou, ela está.
    """
    with conexao() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM telemetria WHERE payload->>'demo' = 'true'")
        return cur.rowcount


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--horas", type=float, default=6.0, help="quanto tempo de histórico gerar"
    )
    parser.add_argument(
        "--limpar", action="store_true", help="apaga os dados de demonstração e sai"
    )
    argumentos = parser.parse_args()

    if argumentos.limpar:
        print(f"Apagados {limpar()} registros de demonstração.")
        return

    quantos = semear(argumentos.horas)
    print(f"Gravados {quantos} registros cobrindo as últimas {argumentos.horas:g} horas.")
    print("Confira com:  curl -H 'Authorization: Bearer SEU_TOKEN' localhost:8000/v1/estado")
    print("Para remover: python3 cloud/scripts/semear-demonstracao.py --limpar")


if __name__ == "__main__":
    main()
