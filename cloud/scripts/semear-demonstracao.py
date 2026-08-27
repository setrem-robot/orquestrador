"""Enche o banco com telemetria plausível, para as telas existirem antes do robô.

O GPS ainda não está montado, e a bateria ainda não é publicada por ninguém.
Sem dados, o app e a landing page mostram tela vazia — e uma tela vazia não diz
se o mapa está errado, se a API está errada, ou se simplesmente não há o que
mostrar. Este script separa essas três coisas.

Ele grava **direto no banco**, o que é o atalho: enche horas de histórico num
instante. O que ele *não* prova é o encanamento — se o broker, o ingestor e os
tópicos estão certos. Para isso existe o `robo-falso.py`, que publica no MQTT e
deixa a corrente inteira funcionar.

O que se gera aqui é plausível, não aleatório: ver `telemetria_falsa.py`.

Uso, na VM ou na sua máquina:

    python3 cloud/scripts/semear-demonstracao.py --horas 6
    python3 cloud/scripts/semear-demonstracao.py --limpar     # tira tudo

Ou apontando para outro banco:

    PGHOST=127.0.0.1 PGPASSWORD=... python3 cloud/scripts/semear-demonstracao.py

Os registros são marcados com `"demo": true` no payload — é o que torna
`--limpar` seguro: ele nunca apaga telemetria de verdade.
"""

from __future__ import annotations

import argparse
import json
import os

import psycopg

import telemetria_falsa as falsa


def conexao() -> psycopg.Connection:
    conninfo = (
        f"host={os.environ.get('PGHOST', '127.0.0.1')} "
        f"port={os.environ.get('PGPORT', '5432')} "
        f"user={os.environ.get('PGUSER', 'robo')} "
        f"password={os.environ.get('PGPASSWORD', '')} "
        f"dbname={os.environ.get('PGDATABASE', 'robo')}"
    )
    return psycopg.connect(conninfo, autocommit=True)


def semear(horas: float) -> int:
    registros = [
        (instante, tipo, topico, json.dumps(payload))
        for instante, tipo, topico, payload in falsa.historico(horas)
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
