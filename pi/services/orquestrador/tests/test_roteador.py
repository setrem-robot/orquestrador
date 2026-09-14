"""Testes de roteador.py.

Cobre o contrato público (`rotear`) e a estrutura orientada a objetos por
trás dele: `ComandoRoteavel` é abstrata, cada subclasse decide sozinha como
tratar seu tipo de comando, e o despacho em `_ROTAS` é polimórfico (mesma
chamada, `.rotear(cmd)`, comportamento diferente por subclasse).

Roda com `python -m unittest` — não precisa de broker MQTT nem de hardware:
roteador.py é lógica pura, conforme diz seu próprio docstring.
"""

from __future__ import annotations

import unittest

from robo_common import topics

from orquestrador.roteador import (
    ComandoMotor,
    ComandoParadaEmergencia,
    ComandoRota,
    ComandoRoteavel,
    ComandoVoz,
    ComandoWifi,
    _ROTAS,
    rotear,
)


class TestComandoRoteavelEhAbstrata(unittest.TestCase):
    def testNaoInstanciaABase(self):
        with self.assertRaises(TypeError):
            ComandoRoteavel()  # type: ignore[abstract]

    def testTodasAsRotasSaoComandoRoteavel(self):
        for tipo, comando in _ROTAS.items():
            with self.subTest(tipo=tipo):
                self.assertIsInstance(comando, ComandoRoteavel)


class TestDespachoPolimorfico(unittest.TestCase):
    """Mesma chamada (`.rotear(cmd)`) em subclasses diferentes -> resultados
    diferentes, sem que quem chama saiba qual subclasse está por trás.
    """

    def testCadaSubclassePublicaNoTopicoDoSeuDominio(self):
        casos: list[tuple[ComandoRoteavel, dict, str]] = [
            (ComandoMotor(), {"acao": "frente", "velocidade": 50}, topics.MOTORES_COMANDO),
            (ComandoVoz(), {"texto": "oi"}, topics.VOZ_FALAR),
            (ComandoParadaEmergencia(), {}, topics.MOTORES_COMANDO),
            (ComandoWifi(), {"acao": "conectar", "ssid": "x"}, topics.WIFI_COMANDO),
            (ComandoRota(), {"acao": "fim"}, topics.ROTA_COMANDO),
        ]
        for comando, entrada, topicoEsperado in casos:
            with self.subTest(tipo=type(comando).__name__):
                publicacoes = comando.rotear(entrada)
                self.assertEqual(publicacoes[0][0], topicoEsperado)


class TestComandoMotor(unittest.TestCase):
    def testAcaoValida(self):
        publicacoes = rotear({"tipo": "motor", "acao": "frente", "velocidade": 80})
        self.assertEqual(publicacoes, [(topics.MOTORES_COMANDO, {"acao": "frente", "velocidade": 80})])

    def testAcaoInvalidaERejeitada(self):
        self.assertEqual(rotear({"tipo": "motor", "acao": "voar"}), [])

    def testVelocidadeForaDaFaixaESaturada(self):
        publicacoes = rotear({"tipo": "motor", "acao": "frente", "velocidade": 999})
        self.assertEqual(publicacoes[0][1]["velocidade"], 100)

    def testVelocidadeInvalidaUsaPadrao(self):
        publicacoes = rotear({"tipo": "motor", "acao": "frente", "velocidade": "abc"})
        self.assertEqual(publicacoes[0][1]["velocidade"], 60)

    def testPararZeraVelocidadeMesmoSeInformada(self):
        publicacoes = rotear({"tipo": "motor", "acao": "parar", "velocidade": 100})
        self.assertEqual(publicacoes[0][1]["velocidade"], 0)


class TestComandoCompacto(unittest.TestCase):
    def testLetraConhecidaViraComandoDeMotor(self):
        publicacoes = rotear({"cmd": "F"})
        self.assertEqual(publicacoes, [(topics.MOTORES_COMANDO, {"acao": "frente", "velocidade": 60})])

    def testLetraMinusculaTambemFunciona(self):
        publicacoes = rotear({"cmd": "s"})
        self.assertEqual(publicacoes[0][1]["acao"], "parar")

    def testLetraDesconhecidaERejeitada(self):
        self.assertEqual(rotear({"cmd": "Z"}), [])


class TestComandoVoz(unittest.TestCase):
    def testTextoValido(self):
        publicacoes = rotear({"tipo": "voz", "texto": "  ola, tudo bem?  "})
        self.assertEqual(publicacoes, [(topics.VOZ_FALAR, {"texto": "ola, tudo bem?"})])

    def testTextoVazioERejeitado(self):
        self.assertEqual(rotear({"tipo": "voz", "texto": "   "}), [])

    def testSemTextoERejeitado(self):
        self.assertEqual(rotear({"tipo": "voz"}), [])


class TestComandoParadaEmergencia(unittest.TestCase):
    def testSempreZeraMotores(self):
        publicacoes = rotear({"tipo": "parada_emergencia"})
        self.assertEqual(publicacoes, [(topics.MOTORES_COMANDO, {"acao": "parar", "velocidade": 0})])


class TestComandoWifi(unittest.TestCase):
    def testRepassaCamposConhecidos(self):
        publicacoes = rotear(
            {"tipo": "wifi", "acao": "conectar", "ssid": "casa", "senha": "123", "campo_ignorado": "x"}
        )
        self.assertEqual(
            publicacoes,
            [(topics.WIFI_COMANDO, {"acao": "conectar", "ssid": "casa", "senha": "123"})],
        )

    def testAcaoPadraoEConectar(self):
        publicacoes = rotear({"tipo": "wifi", "ssid": "casa"})
        self.assertEqual(publicacoes[0][1]["acao"], "conectar")


class TestEntradasMalformadas(unittest.TestCase):
    def testComandoNaoEDict(self):
        self.assertEqual(rotear("nao e um dict"), [])
        self.assertEqual(rotear(None), [])
        self.assertEqual(rotear([1, 2, 3]), [])

    def testTipoDesconhecido(self):
        self.assertEqual(rotear({"tipo": "autodestruicao"}), [])


class TestComandoMoverContinuo(unittest.TestCase):
    """`mover`: o formato que um joystick produz, com dois eixos contínuos."""

    def testRepassaOsDoisEixos(self):
        destino, payload = rotear(
            {"tipo": "motor", "acao": "mover", "linear": 0.8, "angular": -0.3}
        )[0]
        self.assertEqual(destino, topics.MOTORES_COMANDO)
        self.assertEqual(payload, {"acao": "mover", "linear": 0.8, "angular": -0.3})

    def testSaturaOsEixosEmMenosUmEUm(self):
        # O app é não-confiável: um eixo em 5,0 não pode virar cinco vezes a
        # velocidade máxima do outro lado do barramento.
        _, payload = rotear({"tipo": "motor", "acao": "mover", "linear": 5.0})[0]
        self.assertEqual(payload["linear"], 1.0)

    def testEixoAusenteOuTortoViraZero(self):
        # Um `mover` pela metade é uma parada — a interpretação segura de um
        # comando que chegou incompleto.
        _, payload = rotear({"tipo": "motor", "acao": "mover", "linear": "rápido"})[0]
        self.assertEqual(payload, {"acao": "mover", "linear": 0.0, "angular": 0.0})


class TestComandoRota(unittest.TestCase):
    """A rota segura chega fatiada: `inicio`, um `ponto` por waypoint, `fim`.
    Cada mensagem é validada e republicada isoladamente (roteador sem estado).
    """

    def testInicioComTotalENome(self):
        destino, payload = rotear(
            {"tipo": "rota", "acao": "inicio", "total": 3, "nome": "  volta  "}
        )[0]
        self.assertEqual(destino, topics.ROTA_COMANDO)
        self.assertEqual(payload, {"acao": "inicio", "total": 3, "nome": "volta"})

    def testInicioSemNomeOmiteOCampo(self):
        _, payload = rotear({"tipo": "rota", "acao": "inicio", "total": 0})[0]
        self.assertEqual(payload, {"acao": "inicio", "total": 0})

    def testInicioSemTotalValidoEDescartado(self):
        self.assertEqual(rotear({"tipo": "rota", "acao": "inicio"}), [])
        self.assertEqual(rotear({"tipo": "rota", "acao": "inicio", "total": -1}), [])

    def testPontoValido(self):
        _, payload = rotear(
            {"tipo": "rota", "acao": "ponto", "i": 2, "lat": -28.26, "lon": -54.02}
        )[0]
        self.assertEqual(payload, {"acao": "ponto", "i": 2, "lat": -28.26, "lon": -54.02})

    def testPontoComCoordenadaForaDoPlanetaEDescartado(self):
        # Origem é o app (não-confiável): 999 de latitude não é lugar nenhum.
        self.assertEqual(
            rotear({"tipo": "rota", "acao": "ponto", "i": 0, "lat": 999, "lon": 0}), []
        )
        self.assertEqual(
            rotear({"tipo": "rota", "acao": "ponto", "i": 0, "lat": 0, "lon": "x"}), []
        )

    def testPontoSemIndiceEDescartado(self):
        self.assertEqual(
            rotear({"tipo": "rota", "acao": "ponto", "lat": 0, "lon": 0}), []
        )

    def testFim(self):
        destino, payload = rotear({"tipo": "rota", "acao": "fim"})[0]
        self.assertEqual(destino, topics.ROTA_COMANDO)
        self.assertEqual(payload, {"acao": "fim"})

    def testAcaoDesconhecidaEDescartada(self):
        self.assertEqual(rotear({"tipo": "rota", "acao": "apagar"}), [])
        self.assertEqual(rotear({"tipo": "rota"}), [])


if __name__ == "__main__":
    unittest.main()
