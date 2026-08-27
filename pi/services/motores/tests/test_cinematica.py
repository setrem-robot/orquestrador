"""Testes de cinematica.py.

Cobre a tradução de comando em velocidade de cada roda e a rampa de aceleração.
Tudo é função pura ou estado sem relógio próprio, então roda com
`python -m unittest`: sem GPIO, sem motor e sem esperar em tempo real — uma
rampa de meio segundo é testada em zero segundos, movendo o relógio na mão.
"""

from __future__ import annotations

import unittest

from motores.cinematica import Rampa, Velocidades, de_eixos, do_comando


class TestDirecoesDeSempre(unittest.TestCase):
    """As quatro direções que o app manda hoje."""

    def test_frente_move_os_dois_lados_para_a_frente(self):
        self.assertEqual(do_comando("frente", 100), Velocidades(1.0, 1.0))

    def test_tras_move_os_dois_para_tras(self):
        self.assertEqual(do_comando("tras", 100), Velocidades(-1.0, -1.0))

    def test_girar_poe_os_lados_em_sentidos_opostos(self):
        # É o que faz o robô girar no lugar em vez de descrever uma curva.
        self.assertEqual(do_comando("direita", 100), Velocidades(1.0, -1.0))
        self.assertEqual(do_comando("esquerda", 100), Velocidades(-1.0, 1.0))

    def test_parar_zera_os_dois(self):
        self.assertTrue(do_comando("parar").parado)

    def test_velocidade_escala_proporcionalmente(self):
        self.assertEqual(do_comando("frente", 50), Velocidades(0.5, 0.5))

    def test_velocidade_fora_da_faixa_e_saturada(self):
        # O app é não-confiável: 250% de velocidade não pode virar 2,5 no motor.
        self.assertEqual(do_comando("frente", 250), Velocidades(1.0, 1.0))
        self.assertEqual(do_comando("frente", -30), Velocidades(0.0, 0.0))

    def test_acao_desconhecida_e_recusada(self):
        with self.assertRaises(ValueError):
            do_comando("decolar", 100)


class TestModoContinuo(unittest.TestCase):
    """`mover`: o que um joystick produz, e o que permite curvar andando."""

    def test_so_avanco_anda_reto(self):
        self.assertEqual(de_eixos(1.0, 0.0), Velocidades(1.0, 1.0))

    def test_avanco_com_giro_faz_uma_curva(self):
        # Meia-força à frente com um quarto de giro à direita: o lado de dentro
        # da curva anda menos que o de fora, e nenhum dos dois inverte.
        curva = de_eixos(0.5, 0.25)
        self.assertAlmostEqual(curva.esquerda, 0.75)
        self.assertAlmostEqual(curva.direita, 0.25)

    def test_a_soma_que_estouraria_e_reduzida_sem_deformar_a_curva(self):
        # Somar daria 1,5 de um lado. Cortar no teto deixaria 1,0 e 0,5 — uma
        # curva mais fechada do que se pediu. Dividir mantém a proporção.
        curva = de_eixos(1.0, 0.5)
        self.assertAlmostEqual(curva.esquerda, 1.0)
        self.assertAlmostEqual(curva.direita, 1.0 / 3.0)
        self.assertAlmostEqual(curva.esquerda / curva.direita, 1.5 / 0.5)

    def test_eixos_fora_da_faixa_sao_saturados(self):
        self.assertEqual(do_comando("mover", linear=5.0, angular=0.0), Velocidades(1.0, 1.0))

    def test_mover_sem_eixo_nenhum_e_uma_parada(self):
        self.assertTrue(do_comando("mover").parado)


class TestInversaoDeMontagem(unittest.TestCase):
    def test_espelha_apenas_o_lado_pedido(self):
        # Os dois motores ficam de frente um para o outro no chassi; qual deles
        # gira ao contrário depende de como foi parafusado.
        andando = Velocidades(1.0, 1.0)
        self.assertEqual(andando.invertendo(True, False), Velocidades(-1.0, 1.0))
        self.assertEqual(andando.invertendo(False, False), andando)


class TestRampa(unittest.TestCase):
    def test_sobe_no_tempo_pedido(self):
        # 2,0 por segundo: do parado ao máximo em meio segundo.
        rampa = Rampa(2.0)
        rampa.pedir(Velocidades(1.0, 1.0))
        self.assertAlmostEqual(rampa.avancar(0.25).esquerda, 0.5)
        self.assertAlmostEqual(rampa.avancar(0.25).esquerda, 1.0)

    def test_nao_passa_do_alvo(self):
        rampa = Rampa(2.0)
        rampa.pedir(Velocidades(0.3, 0.3))
        self.assertEqual(rampa.avancar(10.0), Velocidades(0.3, 0.3))
        self.assertTrue(rampa.alcancou)

    def test_desce_tambem(self):
        rampa = Rampa(2.0)
        rampa.pedir(Velocidades(1.0, 1.0))
        rampa.avancar(1.0)
        rampa.pedir(Velocidades())
        self.assertAlmostEqual(rampa.avancar(0.25).esquerda, 0.5)

    def test_inverter_o_sentido_passa_pelo_zero(self):
        # O motor não salta de andar para dar ré: a rampa o leva pelo zero.
        rampa = Rampa(2.0)
        rampa.pedir(Velocidades(1.0, 1.0))
        rampa.avancar(1.0)
        rampa.pedir(Velocidades(-1.0, -1.0))
        self.assertAlmostEqual(rampa.avancar(0.5).esquerda, 0.0)
        self.assertAlmostEqual(rampa.avancar(0.5).esquerda, -1.0)

    def test_parar_agora_ignora_a_rampa(self):
        # Quando o motivo de parar é que ninguém está no controle, meio segundo
        # de desaceleração é meio segundo de robô andando sozinho.
        rampa = Rampa(0.5)
        rampa.pedir(Velocidades(1.0, 1.0))
        rampa.avancar(2.0)
        self.assertTrue(rampa.parar_agora().parado)
        self.assertTrue(rampa.avancar(0.05).parado)

    def test_aceleracao_zero_desliga_a_rampa(self):
        # É o comportamento antigo, para um chassi leve o bastante para não
        # perder passo no arranque.
        rampa = Rampa(0.0)
        rampa.pedir(Velocidades(1.0, 1.0))
        self.assertFalse(rampa.ligada)
        self.assertEqual(rampa.atual, Velocidades(1.0, 1.0))


if __name__ == "__main__":
    unittest.main()
