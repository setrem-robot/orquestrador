"""Testes dos porteiros: token, precisão pública e limitador.

`seguranca.py` lê o ambiente no import, então os testes recarregam o módulo com
o ambiente que querem — é o que permite verificar o caso mais importante (API
sem token configurado) sem subir nada.
"""

from __future__ import annotations

import importlib
import os
import unittest


def recarregar(**ambiente):
    """Recarrega `seguranca` com estas variáveis de ambiente."""
    anteriores = {chave: os.environ.get(chave) for chave in ambiente}
    os.environ.update({k: v for k, v in ambiente.items() if v is not None})
    for chave, valor in ambiente.items():
        if valor is None:
            os.environ.pop(chave, None)
    from app import seguranca

    modulo = importlib.reload(seguranca)
    return modulo, anteriores


class TestToken(unittest.TestCase):
    def test_token_certo_passa(self):
        seg, _ = recarregar(API_TOKEN="segredo-do-robo")
        self.assertTrue(seg.token_confere("Bearer segredo-do-robo"))

    def test_token_errado_nao_passa(self):
        seg, _ = recarregar(API_TOKEN="segredo-do-robo")
        self.assertFalse(seg.token_confere("Bearer outro"))

    def test_sem_o_prefixo_bearer_nao_passa(self):
        seg, _ = recarregar(API_TOKEN="segredo-do-robo")
        self.assertFalse(seg.token_confere("segredo-do-robo"))
        self.assertFalse(seg.token_confere(None))

    def test_api_sem_token_configurado_recusa_todo_mundo(self):
        # O contrário — servir o histórico inteiro a quem descobrir o endereço
        # — é o modo de falhar que ninguém percebe até ser tarde.
        seg, _ = recarregar(API_TOKEN="")
        self.assertFalse(seg.token_configurado())
        self.assertFalse(seg.token_confere("Bearer qualquer"))
        self.assertFalse(seg.token_confere(""))


class TestPrecisaoPublica(unittest.TestCase):
    def test_arredonda_para_onze_metros(self):
        seg, _ = recarregar(PRECISAO_GPS_PUBLICA="4")
        self.assertEqual(seg.arredondar_coordenada(-28.2456789), -28.2457)

    def test_zero_desliga(self):
        seg, _ = recarregar(PRECISAO_GPS_PUBLICA="0")
        self.assertEqual(seg.arredondar_coordenada(-28.2456789), -28.2456789)

    def test_sem_coordenada_continua_sem(self):
        seg, _ = recarregar(PRECISAO_GPS_PUBLICA="4")
        self.assertIsNone(seg.arredondar_coordenada(None))


class TestLimitador(unittest.TestCase):
    def test_deixa_passar_ate_o_teto(self):
        from app.seguranca import Limitador

        limitador = Limitador(teto=3, janela_s=60.0)
        self.assertTrue(all(limitador.permitir("1.2.3.4") for _ in range(3)))
        self.assertFalse(limitador.permitir("1.2.3.4"))

    def test_cada_cliente_tem_a_propria_cota(self):
        from app.seguranca import Limitador

        limitador = Limitador(teto=1, janela_s=60.0)
        self.assertTrue(limitador.permitir("1.2.3.4"))
        self.assertFalse(limitador.permitir("1.2.3.4"))
        self.assertTrue(limitador.permitir("5.6.7.8"))

    def test_a_janela_expira(self):
        from app.seguranca import Limitador

        # Janela de zero: toda batida anterior já está velha na batida seguinte.
        limitador = Limitador(teto=1, janela_s=0.0)
        self.assertTrue(limitador.permitir("1.2.3.4"))
        self.assertTrue(limitador.permitir("1.2.3.4"))


if __name__ == "__main__":
    unittest.main()
