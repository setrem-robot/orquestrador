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
    def test_sem_nada_vale_o_ultimo_dia(self):
        inicio, fim = consultas.janela(None, None)
        self.assertAlmostEqual((fim - inicio).total_seconds(), 86400, delta=5)

    def test_janela_invertida_e_endireitada(self):
        # O app mandou o fim antes do início. Recusar seria correto e inútil:
        # o que a pessoa queria ver é o mesmo pedaço de tempo.
        cedo, tarde = instante(hours=5), instante(hours=1)
        self.assertEqual(consultas.janela(tarde, cedo), (cedo, tarde))

    def test_janela_grande_demais_e_cortada(self):
        inicio, fim = consultas.janela(instante(days=900), None)
        self.assertLessEqual(fim - inicio, consultas.MAX_JANELA)

    def test_so_o_inicio_leva_ate_agora(self):
        inicio, fim = consultas.janela(instante(hours=3), None)
        self.assertAlmostEqual((fim - inicio).total_seconds(), 3 * 3600, delta=5)


class TestLimites(unittest.TestCase):
    def test_ausente_usa_o_padrao(self):
        self.assertEqual(consultas.limitar(None, 100, 20), 20)

    def test_pedido_absurdo_e_saturado(self):
        # Um limite de 99 milhões numa hypertable com meses de histórico é uma
        # consulta que ninguém consegue interromper na VM.
        self.assertEqual(consultas.limitar(99_000_000, 100, 20), 100)

    def test_zero_ou_negativo_vira_um(self):
        self.assertEqual(consultas.limitar(0, 100, 20), 1)
        self.assertEqual(consultas.limitar(-5, 100, 20), 1)


class TestTrajeto(unittest.TestCase):
    def test_descarta_pontos_sem_sinal(self):
        # Um GPS sem fix publica lat/lon zerados, e (0, 0) fica no golfo da
        # Guiné — o robô apareceria no mar toda vez que perdesse o sinal.
        sql, _ = consultas.trajeto(None, None, None)
        self.assertIn("fix", sql)

    def test_pega_os_mais_recentes_mas_devolve_em_ordem(self):
        # O LIMIT precisa cortar pelos mais novos; o mapa precisa da linha na
        # ordem em que foi percorrida.
        sql, _ = consultas.trajeto(None, None, 10)
        self.assertIn("ORDER BY ts DESC", sql)
        self.assertTrue(sql.rstrip().endswith("ORDER BY ts ASC"))

    def test_o_limite_vai_como_parametro(self):
        _, parametros = consultas.trajeto(None, None, 42)
        self.assertEqual(parametros[-1], 42)


class TestSerie(unittest.TestCase):
    def test_o_intervalo_vem_da_lista_fechada(self):
        sql, _ = consultas.serie("bateria", "percentual", None, None, "1h")
        self.assertIn("time_bucket('1 hour'", sql)

    def test_campos_validos(self):
        for campo in ("percentual", "velocidade_kmh", "satelites", "tensao_v"):
            self.assertTrue(consultas.campo_valido(campo), campo)

    def test_campo_com_aspas_ou_espaco_e_recusado(self):
        # `campo` é interpolado no SQL (o operador ->> não aceita parâmetro
        # para a chave), então esta validação é a única coisa entre o cliente
        # e uma injeção.
        for campo in ("perc'ual", "a; DROP TABLE telemetria", "a b", "", "x" * 41):
            self.assertFalse(consultas.campo_valido(campo), campo)

    def test_o_tipo_vai_como_parametro(self):
        _, parametros = consultas.serie("bateria", "percentual", None, None, "1d")
        self.assertEqual(parametros[0], "bateria")


class TestEventos(unittest.TestCase):
    def test_sem_filtro_nenhum(self):
        sql, parametros = consultas.eventos(None, None, None)
        self.assertNotIn("tipo = %s", sql)
        self.assertEqual(parametros, (100,))

    def test_filtrar_por_tipo(self):
        sql, parametros = consultas.eventos("gps", 10, None)
        self.assertIn("tipo = %s", sql)
        self.assertEqual(parametros, ("gps", 10))

    def test_paginacao_por_instante(self):
        # Paginar por deslocamento faria a lista pular ou repetir linhas quando
        # telemetria nova chegasse no meio da rolagem — e num robô ligado ela
        # chega o tempo todo.
        corte = instante(minutes=5)
        sql, parametros = consultas.eventos(None, 50, corte)
        self.assertIn("ts < %s", sql)
        self.assertEqual(parametros, (corte, 50))
        self.assertNotIn("OFFSET", sql)


if __name__ == "__main__":
    unittest.main()
