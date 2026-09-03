"""Conexão com o TimescaleDB.

Um pool, e não uma conexão só: a API responde a vários celulares e à landing
page ao mesmo tempo, e uma conexão única serializaria tudo numa fila. O pool
também resolve o caso em que o Postgres reinicia embaixo da API — a conexão
morta é descartada e a próxima requisição pega uma nova, sem ninguém precisar
reiniciar o container.

A API abre o banco **somente para leitura**. Ver `docs/setup-cloud.md` para
criar o usuário `robo_leitura`: é o que garante que uma falha aqui não possa
apagar histórico, por mais que a rota tenha bug.
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Any, Iterator

from psycopg_pool import ConnectionPool

logger = logging.getLogger("api.banco")

#: Teto de tempo de uma consulta. Um gráfico que demorasse mais que isto seria
#: uma pergunta grande demais, e é melhor devolver erro do que prender uma
#: conexão do pool enquanto o celular já desistiu.
TIMEOUT_CONSULTA_MS = 8000

CONNINFO = (
    f"host={os.environ.get('PGHOST', 'timescaledb')} "
    f"port={os.environ.get('PGPORT', '5432')} "
    f"user={os.environ.get('PGUSER', 'robo')} "
    f"password={os.environ.get('PGPASSWORD', '')} "
    f"dbname={os.environ.get('PGDATABASE', 'robo')} "
    # O teto de tempo entra na própria conexão, e não num `SET` por cursor.
    #
    # Antes era `SET LOCAL statement_timeout` dentro do cursor — e `SET LOCAL`
    # vale até o fim da transação corrente. Com `autocommit=True` cada comando
    # é a sua própria transação, então aquele `SET LOCAL` acabava no instante
    # em que era executado: o teto **nunca valeu para a consulta seguinte**.
    # Uma pergunta cara podia prender uma das quatro conexões do pool para
    # sempre, que é exatamente o que este número existe para impedir.
    #
    # Aqui ele é um parâmetro de arranque da sessão: vale para toda conexão
    # que o pool abrir, inclusive as que ele reabre sozinho depois de o
    # Postgres reiniciar.
    f"options=-c statement_timeout={TIMEOUT_CONSULTA_MS}"
)

#: Quantas conexões manter. Quatro cobre com folga um punhado de celulares e a
#: landing page: cada consulta dura milissegundos, então elas se revezam.
POOL_MIN = 1
POOL_MAX = 4

class Banco:
    """Pool de conexões, aberto sob demanda.

    Não conecta no `__init__`: a API precisa subir mesmo com o banco fora do ar
    — respondendo `/saude` com o problema — em vez de morrer no arranque e
    deixar o container em laço de reinício, que é onde ninguém lê o motivo.
    """

    def __init__(self, conninfo: str = CONNINFO) -> None:
        self._pool = ConnectionPool(
            conninfo,
            min_size=POOL_MIN,
            max_size=POOL_MAX,
            open=False,
            kwargs={"autocommit": True},
        )
        self._aberto = False

    def abrir(self) -> None:
        if self._aberto:
            return
        # `wait=False`: se o banco ainda não subiu, as primeiras requisições
        # falham com uma mensagem clara em vez de o arranque inteiro travar.
        self._pool.open(wait=False)
        self._aberto = True

    def fechar(self) -> None:
        if self._aberto:
            self._pool.close()
            self._aberto = False

    @contextmanager
    def cursor(self) -> Iterator[Any]:
        # O `statement_timeout` vem do `CONNINFO`, e não de um `SET` aqui —
        # ver o comentário lá em cima sobre por que `SET LOCAL` não valia.
        with self._pool.connection(timeout=5.0) as conexao:
            with conexao.cursor() as cur:
                yield cur

    def consultar(self, sql: str, parametros: tuple = ()) -> list[tuple]:
        """Roda uma consulta de `consultas.py` e devolve as linhas."""
        with self.cursor() as cur:
            cur.execute(sql, parametros)
            return cur.fetchall()

    def esta_de_pe(self) -> tuple[bool, str]:
        """Se dá para falar com o banco agora. Usado por `/saude`."""
        try:
            with self.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
            return True, ""
        except Exception as exc:
            # Amplo de propósito: o healthcheck existe justamente para relatar
            # o que quebrou, e não para quebrar junto.
            logger.warning("banco indisponível: %s", exc)
            return False, str(exc)[:200]
