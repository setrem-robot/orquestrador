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
    ComandoRoteavel,
    ComandoVoz,
    ComandoWifi,
    _ROTAS,
    rotear,
)


class TestComandoRoteavelEhAbstrata(unittest.TestCase):
    def test_nao_instancia_a_base(self):
        with self.assertRaises(TypeError):
            ComandoRoteavel()  # type: ignore[abstract]

    def test_todas_as_rotas_sao_comando_roteavel(self):
        for tipo, comando in _ROTAS.items():
            with self.subTest(tipo=tipo):
                self.assertIsInstance(comando, ComandoRoteavel)


class TestDespachoPolimorfico(unittest.TestCase):
    """Mesma chamada (`.rotear(cmd)`) em subclasses diferentes -> resultados
    diferentes, sem que quem chama saiba qual subclasse está por trás.
    """

    def test_cada_subclasse_publica_no_topico_do_seu_dominio(self):
        casos: list[tuple[ComandoRoteavel, dict, str]] = [
            (ComandoMotor(), {"acao": "frente", "velocidade": 50}, topics.MOTORES_COMANDO),
            (ComandoVoz(), {"texto": "oi"}, topics.VOZ_FALAR),
            (ComandoParadaEmergencia(), {}, topics.MOTORES_COMANDO),
            (ComandoWifi(), {"acao": "conectar", "ssid": "x"}, topics.WIFI_COMANDO),
        ]
        for comando, entrada, topico_esperado in casos:
            with self.subTest(tipo=type(comando).__name__):
                publicacoes = comando.rotear(entrada)
                self.assertEqual(publicacoes[0][0], topico_esperado)


class TestComandoMotor(unittest.TestCase):
    def test_acao_valida(self):
        publicacoes = rotear({"tipo": "motor", "acao": "frente", "velocidade": 80})
        self.assertEqual(publicacoes, [(topics.MOTORES_COMANDO, {"acao": "frente", "velocidade": 80})])

    def test_acao_invalida_e_rejeitada(self):
        self.assertEqual(rotear({"tipo": "motor", "acao": "voar"}), [])

    def test_velocidade_fora_da_faixa_e_saturada(self):
        publicacoes = rotear({"tipo": "motor", "acao": "frente", "velocidade": 999})
        self.assertEqual(publicacoes[0][1]["velocidade"], 100)

    def test_velocidade_invalida_usa_padrao(self):
        publicacoes = rotear({"tipo": "motor", "acao": "frente", "velocidade": "abc"})
        self.assertEqual(publicacoes[0][1]["velocidade"], 60)

    def test_parar_zera_velocidade_mesmo_se_informada(self):
        publicacoes = rotear({"tipo": "motor", "acao": "parar", "velocidade": 100})
        self.assertEqual(publicacoes[0][1]["velocidade"], 0)


class TestComandoCompacto(unittest.TestCase):
    def test_letra_conhecida_vira_comando_de_motor(self):
        publicacoes = rotear({"cmd": "F"})
        self.assertEqual(publicacoes, [(topics.MOTORES_COMANDO, {"acao": "frente", "velocidade": 60})])

    def test_letra_minuscula_tambem_funciona(self):
        publicacoes = rotear({"cmd": "s"})
        self.assertEqual(publicacoes[0][1]["acao"], "parar")

    def test_letra_desconhecida_e_rejeitada(self):
        self.assertEqual(rotear({"cmd": "Z"}), [])


class TestComandoVoz(unittest.TestCase):
    def test_texto_valido(self):
        publicacoes = rotear({"tipo": "voz", "texto": "  ola, tudo bem?  "})
        self.assertEqual(publicacoes, [(topics.VOZ_FALAR, {"texto": "ola, tudo bem?"})])

    def test_texto_vazio_e_rejeitado(self):
        self.assertEqual(rotear({"tipo": "voz", "texto": "   "}), [])

    def test_sem_texto_e_rejeitado(self):
        self.assertEqual(rotear({"tipo": "voz"}), [])


class TestComandoParadaEmergencia(unittest.TestCase):
    def test_sempre_zera_motores(self):
        publicacoes = rotear({"tipo": "parada_emergencia"})
        self.assertEqual(publicacoes, [(topics.MOTORES_COMANDO, {"acao": "parar", "velocidade": 0})])


class TestComandoWifi(unittest.TestCase):
    def test_repassa_campos_conhecidos(self):
        publicacoes = rotear(
            {"tipo": "wifi", "acao": "conectar", "ssid": "casa", "senha": "123", "campo_ignorado": "x"}
        )
        self.assertEqual(
            publicacoes,
            [(topics.WIFI_COMANDO, {"acao": "conectar", "ssid": "casa", "senha": "123"})],
        )

    def test_acao_padrao_e_conectar(self):
        publicacoes = rotear({"tipo": "wifi", "ssid": "casa"})
        self.assertEqual(publicacoes[0][1]["acao"], "conectar")


class TestEntradasMalformadas(unittest.TestCase):
    def test_comando_nao_e_dict(self):
        self.assertEqual(rotear("nao e um dict"), [])
        self.assertEqual(rotear(None), [])
        self.assertEqual(rotear([1, 2, 3]), [])

    def test_tipo_desconhecido(self):
        self.assertEqual(rotear({"tipo": "autodestruicao"}), [])


if __name__ == "__main__":
    unittest.main()
