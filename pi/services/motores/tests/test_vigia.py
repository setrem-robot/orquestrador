"""Testes de vigia.py.

Cobre a regra que decide se o robô continua andando: enquanto o comando se
repete ele anda; quando o silêncio passa do limite, para.

Roda com `python -m unittest` — não precisa de GPIO, de broker MQTT nem de
esperar em tempo real: o `Vigia` recebe o instante de quem o chama, então os
testes movem o relógio na mão.
"""

from __future__ import annotations

import unittest

from motores.vigia import ACOES_DE_MOVIMENTO, Vigia


class TestEnquantoOsComandosChegam(unittest.TestCase):
    def testMovimentoRecemRecebidoNaoExpira(self):
        vigia = Vigia(timeoutS=1.0)
        vigia.comandoRecebido("frente", agora=10.0)
        self.assertFalse(vigia.expirou(agora=10.5))

    def testComandoRepetidoRenovaOPrazo(self):
        # É isto que mantém o robô andando: o app repete o comando enquanto o
        # dedo está no botão.
        vigia = Vigia(timeoutS=1.0)
        vigia.comandoRecebido("frente", agora=10.0)
        for instante in (10.3, 10.6, 10.9, 11.2, 11.5):
            vigia.comandoRecebido("frente", agora=instante)
            self.assertFalse(vigia.expirou(agora=instante))

    def testTodasAsAcoesDeMovimentoArmamOVigia(self):
        for acao in ACOES_DE_MOVIMENTO:
            with self.subTest(acao=acao):
                vigia = Vigia(timeoutS=1.0)
                vigia.comandoRecebido(acao, agora=0.0)
                self.assertTrue(vigia.vigiando)


class TestQuandoOControleEmudece(unittest.TestCase):
    def testSilencioAlemDoLimiteMandaParar(self):
        vigia = Vigia(timeoutS=1.0)
        vigia.comandoRecebido("frente", agora=10.0)
        self.assertTrue(vigia.expirou(agora=11.01))

    def testNoLimiteExatoJaExpirou(self):
        vigia = Vigia(timeoutS=1.0)
        vigia.comandoRecebido("frente", agora=10.0)
        self.assertTrue(vigia.expirou(agora=11.0))

    def testAvisaUmaVezSo(self):
        # Quem chama vai mandar parar; repetir o aviso a cada volta do laço
        # encheria o log e o tópico de status com a mesma parada.
        vigia = Vigia(timeoutS=1.0)
        vigia.comandoRecebido("frente", agora=10.0)
        self.assertTrue(vigia.expirou(agora=12.0))
        self.assertFalse(vigia.expirou(agora=13.0))
        self.assertFalse(vigia.expirou(agora=99.0))

    def testVoltaAVigiarNoMovimentoSeguinte(self):
        vigia = Vigia(timeoutS=1.0)
        vigia.comandoRecebido("frente", agora=10.0)
        vigia.expirou(agora=12.0)

        vigia.comandoRecebido("tras", agora=20.0)
        self.assertFalse(vigia.expirou(agora=20.5))
        self.assertTrue(vigia.expirou(agora=21.5))


class TestParadaDesarma(unittest.TestCase):
    def testDepoisDePararNaoHaOQueVigiar(self):
        vigia = Vigia(timeoutS=1.0)
        vigia.comandoRecebido("frente", agora=10.0)
        vigia.comandoRecebido("parar", agora=10.2)
        self.assertFalse(vigia.vigiando)
        self.assertFalse(vigia.expirou(agora=99.0))

    def testAcaoDesconhecidaTambemDesarma(self):
        # Conservador de propósito: se não dá para afirmar que o robô ficou em
        # movimento, vigiar só produziria uma parada sem motivo.
        vigia = Vigia(timeoutS=1.0)
        vigia.comandoRecebido("frente", agora=10.0)
        vigia.comandoRecebido("cambalhota", agora=10.2)
        self.assertFalse(vigia.vigiando)


class TestDesligado(unittest.TestCase):
    def testTimeoutZeroNuncaExpira(self):
        vigia = Vigia(timeoutS=0.0)
        vigia.comandoRecebido("frente", agora=10.0)
        self.assertFalse(vigia.ligado)
        self.assertFalse(vigia.expirou(agora=9999.0))

    def testTimeoutNegativoTambemDesliga(self):
        vigia = Vigia(timeoutS=-1.0)
        vigia.comandoRecebido("frente", agora=10.0)
        self.assertFalse(vigia.expirou(agora=9999.0))


class TestNadaAconteceuAinda(unittest.TestCase):
    def testVigiaNovoNaoMandaParar(self):
        # No arranque do serviço o robô está parado; expirar aqui publicaria uma
        # parada sem que ninguém tenha mandado nada.
        vigia = Vigia(timeoutS=1.0)
        self.assertFalse(vigia.vigiando)
        self.assertFalse(vigia.expirou(agora=9999.0))


if __name__ == "__main__":
    unittest.main()
