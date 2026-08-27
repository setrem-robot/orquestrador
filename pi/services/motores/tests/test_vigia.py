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
    def test_movimento_recem_recebido_nao_expira(self):
        vigia = Vigia(timeout_s=1.0)
        vigia.comando_recebido("frente", agora=10.0)
        self.assertFalse(vigia.expirou(agora=10.5))

    def test_comando_repetido_renova_o_prazo(self):
        # É isto que mantém o robô andando: o app repete o comando enquanto o
        # dedo está no botão.
        vigia = Vigia(timeout_s=1.0)
        vigia.comando_recebido("frente", agora=10.0)
        for instante in (10.3, 10.6, 10.9, 11.2, 11.5):
            vigia.comando_recebido("frente", agora=instante)
            self.assertFalse(vigia.expirou(agora=instante))

    def test_todas_as_acoes_de_movimento_armam_o_vigia(self):
        for acao in ACOES_DE_MOVIMENTO:
            with self.subTest(acao=acao):
                vigia = Vigia(timeout_s=1.0)
                vigia.comando_recebido(acao, agora=0.0)
                self.assertTrue(vigia.vigiando)


class TestQuandoOControleEmudece(unittest.TestCase):
    def test_silencio_alem_do_limite_manda_parar(self):
        vigia = Vigia(timeout_s=1.0)
        vigia.comando_recebido("frente", agora=10.0)
        self.assertTrue(vigia.expirou(agora=11.01))

    def test_no_limite_exato_ja_expirou(self):
        vigia = Vigia(timeout_s=1.0)
        vigia.comando_recebido("frente", agora=10.0)
        self.assertTrue(vigia.expirou(agora=11.0))

    def test_avisa_uma_vez_so(self):
        # Quem chama vai mandar parar; repetir o aviso a cada volta do laço
        # encheria o log e o tópico de status com a mesma parada.
        vigia = Vigia(timeout_s=1.0)
        vigia.comando_recebido("frente", agora=10.0)
        self.assertTrue(vigia.expirou(agora=12.0))
        self.assertFalse(vigia.expirou(agora=13.0))
        self.assertFalse(vigia.expirou(agora=99.0))

    def test_volta_a_vigiar_no_movimento_seguinte(self):
        vigia = Vigia(timeout_s=1.0)
        vigia.comando_recebido("frente", agora=10.0)
        vigia.expirou(agora=12.0)

        vigia.comando_recebido("tras", agora=20.0)
        self.assertFalse(vigia.expirou(agora=20.5))
        self.assertTrue(vigia.expirou(agora=21.5))


class TestParadaDesarma(unittest.TestCase):
    def test_depois_de_parar_nao_ha_o_que_vigiar(self):
        vigia = Vigia(timeout_s=1.0)
        vigia.comando_recebido("frente", agora=10.0)
        vigia.comando_recebido("parar", agora=10.2)
        self.assertFalse(vigia.vigiando)
        self.assertFalse(vigia.expirou(agora=99.0))

    def test_acao_desconhecida_tambem_desarma(self):
        # Conservador de propósito: se não dá para afirmar que o robô ficou em
        # movimento, vigiar só produziria uma parada sem motivo.
        vigia = Vigia(timeout_s=1.0)
        vigia.comando_recebido("frente", agora=10.0)
        vigia.comando_recebido("cambalhota", agora=10.2)
        self.assertFalse(vigia.vigiando)


class TestDesligado(unittest.TestCase):
    def test_timeout_zero_nunca_expira(self):
        vigia = Vigia(timeout_s=0.0)
        vigia.comando_recebido("frente", agora=10.0)
        self.assertFalse(vigia.ligado)
        self.assertFalse(vigia.expirou(agora=9999.0))

    def test_timeout_negativo_tambem_desliga(self):
        vigia = Vigia(timeout_s=-1.0)
        vigia.comando_recebido("frente", agora=10.0)
        self.assertFalse(vigia.expirou(agora=9999.0))


class TestNadaAconteceuAinda(unittest.TestCase):
    def test_vigia_novo_nao_manda_parar(self):
        # No arranque do serviço o robô está parado; expirar aqui publicaria uma
        # parada sem que ninguém tenha mandado nada.
        vigia = Vigia(timeout_s=1.0)
        self.assertFalse(vigia.vigiando)
        self.assertFalse(vigia.expirou(agora=9999.0))


if __name__ == "__main__":
    unittest.main()
