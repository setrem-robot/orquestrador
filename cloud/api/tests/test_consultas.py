"""Testes das consultas e dos porteiros da API.

Nada aqui toca no TimescaleDB. `consultas.py` devolve `(sql, parametros)` sem
executar nada, e `seguranca.py` é decisão pura — então a parte que erra por
descuido (limite não saturado, janela invertida, campo entrando no SQL sem
validação) é testável com `python -m unittest`, sem banco e sem container.

    cd cloud/api && python -m unittest discover -s tests
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from app import consultas


def instante(**delta) -> datetime:
    return datetime.now(tz=timezone.utc) - timedelta(**delta)


class TestJanela(unittest.TestCase):
    def testSemNadaValeOUltimoDia(self):
        inicio, fim = consultas.janela(None, None)
        self.assertAlmostEqual((fim - inicio).total_seconds(), 86400, delta=5)

    def testJanelaInvertidaEEndireitada(self):
        # O app mandou o fim antes do início. Recusar seria correto e inútil:
        # o que a pessoa queria ver é o mesmo pedaço de tempo.
        cedo, tarde = instante(hours=5), instante(hours=1)
        self.assertEqual(consultas.janela(tarde, cedo), (cedo, tarde))

    def testJanelaGrandeDemaisECortada(self):
        inicio, fim = consultas.janela(instante(days=900), None)
        self.assertLessEqual(fim - inicio, consultas.MAX_JANELA)

    def testSoOInicioLevaAteAgora(self):
        inicio, fim = consultas.janela(instante(hours=3), None)
        self.assertAlmostEqual((fim - inicio).total_seconds(), 3 * 3600, delta=5)


class TestLimites(unittest.TestCase):
    def testAusenteUsaOPadrao(self):
        self.assertEqual(consultas.limitar(None, 100, 20), 20)

    def testPedidoAbsurdoESaturado(self):
        # Um limite de 99 milhões numa hypertable com meses de histórico é uma
        # consulta que ninguém consegue interromper na VM.
        self.assertEqual(consultas.limitar(99_000_000, 100, 20), 100)

    def testZeroOuNegativoViraUm(self):
        self.assertEqual(consultas.limitar(0, 100, 20), 1)
        self.assertEqual(consultas.limitar(-5, 100, 20), 1)


class TestTrajeto(unittest.TestCase):
    def testDescartaPontosSemSinal(self):
        # Um GPS sem fix publica lat/lon zerados, e (0, 0) fica no golfo da
        # Guiné — o robô apareceria no mar toda vez que perdesse o sinal.
        sql, _ = consultas.trajeto(None, None, None)
        self.assertIn("fix", sql)

    def testPegaOsMaisRecentesMasDevolveEmOrdem(self):
        # O LIMIT precisa cortar pelos mais novos; o mapa precisa da linha na
        # ordem em que foi percorrida.
        sql, _ = consultas.trajeto(None, None, 10)
        self.assertIn("ORDER BY ts DESC", sql)
        self.assertTrue(sql.rstrip().endswith("ORDER BY ts ASC"))

    def testOLimiteVaiComoParametro(self):
        _, parametros = consultas.trajeto(None, None, 42)
        self.assertEqual(parametros[-1], 42)


class TestSerie(unittest.TestCase):
    def testOIntervaloVemDaListaFechada(self):
        sql, _ = consultas.serie("bateria", "percentual", None, None, "1h")
        self.assertIn("time_bucket('1 hour'", sql)

    def testCamposValidos(self):
        for campo in ("percentual", "velocidade_kmh", "satelites", "tensao_v"):
            self.assertTrue(consultas.campoValido(campo), campo)

    def testCampoAninhadoEValido(self):
        # A saúde do Pi guarda os números dentro de blocos; o gráfico de CPU e
        # de memória depende de alcançar `cpu.uso_pct` e `memoria.uso_pct`.
        for campo in ("cpu.uso_pct", "memoria.uso_pct", "cpu.freq_mhz"):
            self.assertTrue(consultas.campoValido(campo), campo)

    def testCampoAninhadoViraNavegacaoJson(self):
        sql, _ = consultas.serie("sistema", "cpu.uso_pct", None, None, "1h")
        self.assertIn("payload->'cpu'->>'uso_pct'", sql)
        self.assertIn("payload->'cpu' ? 'uso_pct'", sql)

    def testCampoComAspasOuEspacoERecusado(self):
        # `campo` é interpolado no SQL (o operador ->> não aceita parâmetro
        # para a chave), então esta validação é a única coisa entre o cliente
        # e uma injeção.
        for campo in (
            "perc'ual",
            "a; DROP TABLE telemetria",
            "a b",
            "",
            "x" * 41,
            "cpu.",
            ".uso_pct",
            "a.b.c.d",  # níveis demais
            "cpu.us'o",
        ):
            self.assertFalse(consultas.campoValido(campo), campo)

    def testOTipoVaiComoParametro(self):
        _, parametros = consultas.serie("bateria", "percentual", None, None, "1d")
        self.assertEqual(parametros[0], "bateria")


class TestEventos(unittest.TestCase):
    def testSemFiltroNenhum(self):
        sql, parametros = consultas.eventos(None, None, None)
        self.assertNotIn("tipo = %s", sql)
        self.assertEqual(parametros, (100,))

    def testFiltrarPorTipo(self):
        sql, parametros = consultas.eventos("gps", 10, None)
        self.assertIn("tipo = %s", sql)
        self.assertEqual(parametros, ("gps", 10))

    def testPaginacaoPorInstante(self):
        # Paginar por deslocamento faria a lista pular ou repetir linhas quando
        # telemetria nova chegasse no meio da rolagem — e num robô ligado ela
        # chega o tempo todo.
        corte = instante(minutes=5)
        sql, parametros = consultas.eventos(None, 50, corte)
        self.assertIn("ts < %s", sql)
        self.assertEqual(parametros, (corte, 50))
        self.assertNotIn("OFFSET", sql)


class TestSaudeSistema(unittest.TestCase):
    def testPegaAUltimaDoTipoSistema(self):
        sql, parametros = consultas.saudeSistema()
        # Sem parâmetro: o tipo é fixo (só existe um "sistema"), então vai
        # literal no SQL — e uma varredura de índice com LIMIT 1.
        self.assertEqual(parametros, ())
        self.assertIn("tipo = 'sistema'", sql)
        self.assertIn("ORDER BY ts DESC", sql)
        self.assertIn("LIMIT 1", sql)


if __name__ == "__main__":
    unittest.main()
