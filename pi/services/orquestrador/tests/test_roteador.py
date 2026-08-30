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
            (ComandoRota(), {"acao": "fim"}, topics.ROTA_COMANDO),
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


class TestComandoMoverContinuo(unittest.TestCase):
    """`mover`: o formato que um joystick produz, com dois eixos contínuos."""

    def test_repassa_os_dois_eixos(self):
        destino, payload = rotear(
            {"tipo": "motor", "acao": "mover", "linear": 0.8, "angular": -0.3}
        )[0]
        self.assertEqual(destino, topics.MOTORES_COMANDO)
        self.assertEqual(payload, {"acao": "mover", "linear": 0.8, "angular": -0.3})

    def test_satura_os_eixos_em_menos_um_e_um(self):
        # O app é não-confiável: um eixo em 5,0 não pode virar cinco vezes a
        # velocidade máxima do outro lado do barramento.
        _, payload = rotear({"tipo": "motor", "acao": "mover", "linear": 5.0})[0]
        self.assertEqual(payload["linear"], 1.0)

    def test_eixo_ausente_ou_torto_vira_zero(self):
        # Um `mover` pela metade é uma parada — a interpretação segura de um
        # comando que chegou incompleto.
        _, payload = rotear({"tipo": "motor", "acao": "mover", "linear": "rápido"})[0]
        self.assertEqual(payload, {"acao": "mover", "linear": 0.0, "angular": 0.0})


class TestComandoRota(unittest.TestCase):
    """A rota segura chega fatiada: `inicio`, um `ponto` por waypoint, `fim`.
    Cada mensagem é validada e republicada isoladamente (roteador sem estado).
    """

    def test_inicio_com_total_e_nome(self):
        destino, payload = rotear(
            {"tipo": "rota", "acao": "inicio", "total": 3, "nome": "  volta  "}
        )[0]
        self.assertEqual(destino, topics.ROTA_COMANDO)
        self.assertEqual(payload, {"acao": "inicio", "total": 3, "nome": "volta"})

    def test_inicio_sem_nome_omite_o_campo(self):
        _, payload = rotear({"tipo": "rota", "acao": "inicio", "total": 0})[0]
        self.assertEqual(payload, {"acao": "inicio", "total": 0})

    def test_inicio_sem_total_valido_e_descartado(self):
        self.assertEqual(rotear({"tipo": "rota", "acao": "inicio"}), [])
        self.assertEqual(rotear({"tipo": "rota", "acao": "inicio", "total": -1}), [])

    def test_ponto_valido(self):
        _, payload = rotear(
            {"tipo": "rota", "acao": "ponto", "i": 2, "lat": -28.26, "lon": -54.02}
        )[0]
        self.assertEqual(payload, {"acao": "ponto", "i": 2, "lat": -28.26, "lon": -54.02})

    def test_ponto_com_coordenada_fora_do_planeta_e_descartado(self):
        # Origem é o app (não-confiável): 999 de latitude não é lugar nenhum.
        self.assertEqual(
            rotear({"tipo": "rota", "acao": "ponto", "i": 0, "lat": 999, "lon": 0}), []
        )
        self.assertEqual(
            rotear({"tipo": "rota", "acao": "ponto", "i": 0, "lat": 0, "lon": "x"}), []
        )

    def test_ponto_sem_indice_e_descartado(self):
        self.assertEqual(
            rotear({"tipo": "rota", "acao": "ponto", "lat": 0, "lon": 0}), []
        )

    def test_fim(self):
        destino, payload = rotear({"tipo": "rota", "acao": "fim"})[0]
        self.assertEqual(destino, topics.ROTA_COMANDO)
        self.assertEqual(payload, {"acao": "fim"})

    def test_acao_desconhecida_e_descartada(self):
        self.assertEqual(rotear({"tipo": "rota", "acao": "apagar"}), [])
        self.assertEqual(rotear({"tipo": "rota"}), [])


if __name__ == "__main__":
    unittest.main()
