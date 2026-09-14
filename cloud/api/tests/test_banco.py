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
    def testOTetoVaiNaConexao(self):
        self.assertIn(f"statement_timeout={banco.TIMEOUT_CONSULTA_MS}", banco.CONNINFO)

    def testAConninfoEValidaParaOLibpq(self):
        """O teste que faltava — e que deixou passar um 503 em produção.

        Afirmar que a string CONTÉM `options=-c statement_timeout=` não prova
        nada: essa forma, sem aspas, é justamente a quebrada. O libpq quebra a
        conninfo em espaços, então o valor `-c statement_timeout=8000` precisa
        estar entre aspas — senão vira dois campos e o segundo é inválido.

        `conninfo_to_dict` faz exatamente o parsing que o libpq faz. Se o campo
        `options` não englobar o `-c statement_timeout`, ou se sobrar uma chave
        solta `statement_timeout`, este teste falha — como falharia a conexão.
        """
        from psycopg.conninfo import conninfo_to_dict

        partes = conninfo_to_dict(banco.CONNINFO)

        self.assertNotIn(
            "statement_timeout",
            partes,
            "o teto vazou como campo solto — a conexão inteira seria recusada",
        )
        self.assertEqual(
            partes.get("options"),
            f"-c statement_timeout={banco.TIMEOUT_CONSULTA_MS}",
        )

    def testOCursorNaoUsaMaisSetLocal(self):
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
        with mock.patch.object(b.pool, "connection", return_value=ConexaoFalsa()):
            b.consultar("SELECT 1")

        self.assertNotIn(
            "SET LOCAL",
            " ".join(executados).upper(),
            "o teto voltou para o cursor, onde não vale",
        )
        self.assertEqual(executados, ["SELECT 1"])


class TestConninfo(unittest.TestCase):
    def testTrazOQueOPsycopgPrecisa(self):
        for chave in ("host=", "port=", "user=", "dbname="):
            self.assertIn(chave, banco.CONNINFO)


if __name__ == "__main__":
    unittest.main()
