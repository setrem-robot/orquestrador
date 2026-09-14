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
    def testTokenCertoPassa(self):
        seg, _ = recarregar(API_TOKEN="segredo-do-robo")
        self.assertTrue(seg.tokenConfere("Bearer segredo-do-robo"))

    def testTokenErradoNaoPassa(self):
        seg, _ = recarregar(API_TOKEN="segredo-do-robo")
        self.assertFalse(seg.tokenConfere("Bearer outro"))

    def testSemOPrefixoBearerNaoPassa(self):
        seg, _ = recarregar(API_TOKEN="segredo-do-robo")
        self.assertFalse(seg.tokenConfere("segredo-do-robo"))
        self.assertFalse(seg.tokenConfere(None))

    def testApiSemTokenConfiguradoRecusaTodoMundo(self):
        # O contrário — servir o histórico inteiro a quem descobrir o endereço
        # — é o modo de falhar que ninguém percebe até ser tarde.
        seg, _ = recarregar(API_TOKEN="")
        self.assertFalse(seg.tokenConfigurado())
        self.assertFalse(seg.tokenConfere("Bearer qualquer"))
        self.assertFalse(seg.tokenConfere(""))


class TestPrecisaoPublica(unittest.TestCase):
    def testArredondaParaOnzeMetros(self):
        seg, _ = recarregar(PRECISAO_GPS_PUBLICA="4")
        self.assertEqual(seg.arredondarCoordenada(-28.2456789), -28.2457)

    def testZeroDesliga(self):
        seg, _ = recarregar(PRECISAO_GPS_PUBLICA="0")
        self.assertEqual(seg.arredondarCoordenada(-28.2456789), -28.2456789)

    def testSemCoordenadaContinuaSem(self):
        seg, _ = recarregar(PRECISAO_GPS_PUBLICA="4")
        self.assertIsNone(seg.arredondarCoordenada(None))


class TestLimitador(unittest.TestCase):
    def testDeixaPassarAteOTeto(self):
        from app.seguranca import Limitador

        limitador = Limitador(teto=3, janelaS=60.0)
        self.assertTrue(all(limitador.permitir("1.2.3.4") for _ in range(3)))
        self.assertFalse(limitador.permitir("1.2.3.4"))

    def testCadaClienteTemAPropriaCota(self):
        from app.seguranca import Limitador

        limitador = Limitador(teto=1, janelaS=60.0)
        self.assertTrue(limitador.permitir("1.2.3.4"))
        self.assertFalse(limitador.permitir("1.2.3.4"))
        self.assertTrue(limitador.permitir("5.6.7.8"))

    def testAJanelaExpira(self):
        from app.seguranca import Limitador

        # Janela de zero: toda batida anterior já está velha na batida seguinte.
        #
        # Este teste falhava no Windows e passava no Linux, e a culpa não era do
        # teste: `time.monotonic()` anda de 15 em 15 ms no Windows/Python 3.12,
        # então duas chamadas seguidas devolvem o mesmo instante. Com a expiração
        # escrita como `> janela`, uma batida de idade exatamente zero nunca saía
        # de uma janela de tamanho zero, e a fila não esvaziava nunca.
        limitador = Limitador(teto=1, janelaS=0.0)
        self.assertTrue(limitador.permitir("1.2.3.4"))
        self.assertTrue(limitador.permitir("1.2.3.4"))


class TestCacheCurto(unittest.TestCase):
    """O resumo público é a consulta mais cara servida sem token."""

    def testDevolveOQueGuardou(self):
        from app.seguranca import CacheCurto

        cache = CacheCurto(60.0)
        self.assertIsNone(cache.obter())
        cache.guardar({"tipos": []})
        self.assertEqual(cache.obter(), {"tipos": []})

    def testValidadeZeroNaoGuardaNada(self):
        """Serve para desligar o cache pela configuração, sem tirar o código."""
        from app.seguranca import CacheCurto

        cache = CacheCurto(0.0)
        cache.guardar({"tipos": []})
        self.assertIsNone(cache.obter())

    def testOValorVenceComOTempo(self):
        from app import seguranca

        cache = seguranca.CacheCurto(10.0)
        cache.guardar("velho")
        # Sem esperar de verdade: o relógio é o do módulo, e ele pode andar.
        cache.gravadoEm -= 11.0
        self.assertIsNone(cache.obter())


if __name__ == "__main__":
    unittest.main()
