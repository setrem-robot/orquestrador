"""O teto de tempo das consultas precisa valer de verdade.

Este arquivo existe por um defeito silencioso: o teto estava escrito, estava
comentado, tinha um número plausível — e não valia para consulta nenhuma.

    cur.execute(f"SET LOCAL statement_timeout = {TIMEOUT_CONSULTA_MS}")

`SET LOCAL` vale até o fim da transação corrente. O pool abre as conexões com
`autocommit=True`, e aí cada comando é a sua própria transação: aquele `SET
LOCAL` terminava no instante em que era executado, e o `execute` seguinte —
a consulta de verdade — rodava sem teto nenhum. Uma pergunta cara podia prender
uma das quatro conexões do pool indefinidamente, que é exatamente o que aquele
número existia para impedir.

O teto agora entra no `options` da própria conexão, então vale para toda
conexão que o pool abrir, inclusive as que ele reabre sozinho depois de o
Postgres reiniciar. Nada aqui conecta em banco nenhum: o que se afere é o texto
da conexão e o fato de o cursor não mexer mais nisso.
"""

from __future__ import annotations

import unittest
from unittest import mock

from app import banco


class TestTetoDeTempo(unittest.TestCase):
    def test_o_teto_vai_na_conexao(self):
        self.assertIn(f"statement_timeout={banco.TIMEOUT_CONSULTA_MS}", banco.CONNINFO)

    def test_o_teto_vai_como_opcao_de_sessao(self):
        """`options=-c ...` é o que o Postgres aplica ao abrir a sessão."""
        self.assertIn("options=-c statement_timeout=", banco.CONNINFO)

    def test_o_cursor_nao_usa_mais_set_local(self):
        """`SET LOCAL` sozinho, em autocommit, não sobrevive ao próprio comando."""
        executados: list[str] = []

        class CursorFalso:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def execute(self, sql, params=()):
                executados.append(sql)

            def fetchall(self):
                return []

            def fetchone(self):
                return (1,)

        class ConexaoFalsa:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def cursor(self):
                return CursorFalso()

        b = banco.Banco()
        with mock.patch.object(b._pool, "connection", return_value=ConexaoFalsa()):
            b.consultar("SELECT 1")

        self.assertNotIn(
            "SET LOCAL",
            " ".join(executados).upper(),
            "o teto voltou para o cursor, onde não vale",
        )
        self.assertEqual(executados, ["SELECT 1"])


class TestConninfo(unittest.TestCase):
    def test_traz_o_que_o_psycopg_precisa(self):
        for chave in ("host=", "port=", "user=", "dbname="):
            self.assertIn(chave, banco.CONNINFO)


if __name__ == "__main__":
    unittest.main()
