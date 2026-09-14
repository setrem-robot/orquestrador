"""Testes de cinematica.py.

Cobre a tradução de comando em velocidade de cada roda e a rampa de aceleração.
Tudo é função pura ou estado sem relógio próprio, então roda com
`python -m unittest`: sem GPIO, sem motor e sem esperar em tempo real — uma
rampa de meio segundo é testada em zero segundos, movendo o relógio na mão.
"""

from __future__ import annotations

import unittest

from motores.cinematica import Rampa, Velocidades, deEixos, doComando


class TestDirecoesDeSempre(unittest.TestCase):
    """As quatro direções que o app manda hoje."""

    def testFrenteMoveOsDoisLadosParaAFrente(self):
        self.assertEqual(doComando("frente", 100), Velocidades(1.0, 1.0))

    def testTrasMoveOsDoisParaTras(self):
        self.assertEqual(doComando("tras", 100), Velocidades(-1.0, -1.0))

    def testGirarPoeOsLadosEmSentidosOpostos(self):
        # É o que faz o robô girar no lugar em vez de descrever uma curva.
        self.assertEqual(doComando("direita", 100), Velocidades(1.0, -1.0))
        self.assertEqual(doComando("esquerda", 100), Velocidades(-1.0, 1.0))

    def testPararZeraOsDois(self):
        self.assertTrue(doComando("parar").parado)

    def testVelocidadeEscalaProporcionalmente(self):
        self.assertEqual(doComando("frente", 50), Velocidades(0.5, 0.5))

    def testVelocidadeForaDaFaixaESaturada(self):
        # O app é não-confiável: 250% de velocidade não pode virar 2,5 no motor.
        self.assertEqual(doComando("frente", 250), Velocidades(1.0, 1.0))
        self.assertEqual(doComando("frente", -30), Velocidades(0.0, 0.0))

    def testAcaoDesconhecidaERecusada(self):
        with self.assertRaises(ValueError):
            doComando("decolar", 100)


class TestModoContinuo(unittest.TestCase):
    """`mover`: o que um joystick produz, e o que permite curvar andando."""

    def testSoAvancoAndaReto(self):
        self.assertEqual(deEixos(1.0, 0.0), Velocidades(1.0, 1.0))

    def testAvancoComGiroFazUmaCurva(self):
        # Meia-força à frente com um quarto de giro à direita: o lado de dentro
        # da curva anda menos que o de fora, e nenhum dos dois inverte.
        curva = deEixos(0.5, 0.25)
        self.assertAlmostEqual(curva.esquerda, 0.75)
        self.assertAlmostEqual(curva.direita, 0.25)

    def testASomaQueEstourariaEReduzidaSemDeformarACurva(self):
        # Somar daria 1,5 de um lado. Cortar no teto deixaria 1,0 e 0,5 — uma
        # curva mais fechada do que se pediu. Dividir mantém a proporção.
        curva = deEixos(1.0, 0.5)
        self.assertAlmostEqual(curva.esquerda, 1.0)
        self.assertAlmostEqual(curva.direita, 1.0 / 3.0)
        self.assertAlmostEqual(curva.esquerda / curva.direita, 1.5 / 0.5)

    def testEixosForaDaFaixaSaoSaturados(self):
        self.assertEqual(doComando("mover", linear=5.0, angular=0.0), Velocidades(1.0, 1.0))

    def testMoverSemEixoNenhumEUmaParada(self):
        self.assertTrue(doComando("mover").parado)


class TestInversaoDeMontagem(unittest.TestCase):
    def testEspelhaApenasOLadoPedido(self):
        # Os dois motores ficam de frente um para o outro no chassi; qual deles
        # gira ao contrário depende de como foi parafusado.
        andando = Velocidades(1.0, 1.0)
        self.assertEqual(andando.invertendo(True, False), Velocidades(-1.0, 1.0))
        self.assertEqual(andando.invertendo(False, False), andando)


class TestRampa(unittest.TestCase):
    def testSobeNoTempoPedido(self):
        # 2,0 por segundo: do parado ao máximo em meio segundo.
        rampa = Rampa(2.0)
        rampa.pedir(Velocidades(1.0, 1.0))
        self.assertAlmostEqual(rampa.avancar(0.25).esquerda, 0.5)
        self.assertAlmostEqual(rampa.avancar(0.25).esquerda, 1.0)

    def testNaoPassaDoAlvo(self):
        rampa = Rampa(2.0)
        rampa.pedir(Velocidades(0.3, 0.3))
        self.assertEqual(rampa.avancar(10.0), Velocidades(0.3, 0.3))
        self.assertTrue(rampa.alcancou)

    def testDesceTambem(self):
        rampa = Rampa(2.0)
        rampa.pedir(Velocidades(1.0, 1.0))
        rampa.avancar(1.0)
        rampa.pedir(Velocidades())
        self.assertAlmostEqual(rampa.avancar(0.25).esquerda, 0.5)

    def testInverterOSentidoPassaPeloZero(self):
        # O motor não salta de andar para dar ré: a rampa o leva pelo zero.
        rampa = Rampa(2.0)
        rampa.pedir(Velocidades(1.0, 1.0))
        rampa.avancar(1.0)
        rampa.pedir(Velocidades(-1.0, -1.0))
        self.assertAlmostEqual(rampa.avancar(0.5).esquerda, 0.0)
        self.assertAlmostEqual(rampa.avancar(0.5).esquerda, -1.0)

    def testPararAgoraIgnoraARampa(self):
        # Quando o motivo de parar é que ninguém está no controle, meio segundo
        # de desaceleração é meio segundo de robô andando sozinho.
        rampa = Rampa(0.5)
        rampa.pedir(Velocidades(1.0, 1.0))
        rampa.avancar(2.0)
        self.assertTrue(rampa.pararAgora().parado)
        self.assertTrue(rampa.avancar(0.05).parado)

    def testAceleracaoZeroDesligaARampa(self):
        # É o comportamento antigo, para um chassi leve o bastante para não
        # perder passo no arranque.
        rampa = Rampa(0.0)
        rampa.pedir(Velocidades(1.0, 1.0))
        self.assertFalse(rampa.ligada)
        self.assertEqual(rampa.atual, Velocidades(1.0, 1.0))


if __name__ == "__main__":
    unittest.main()
