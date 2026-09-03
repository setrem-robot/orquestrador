"""Testes do provisionamento de Wi-Fi, sem NetworkManager e sem placa.

`processar()` é lógica pura assim que o `nmcli` sai do caminho: recebe um dict
que veio do app (cliente **não-confiável**, atravessando um rádio BLE que não
pede senha) e devolve outro dict. Só o `_run_nmcli` toca o sistema, e é ele que
estes testes substituem.

Isto fecha uma lacuna que o `CLAUDE.md` registrava como "só validável no Pi
físico": era verdade para `escanear_redes` e `conectar`, não para a validação
do comando — que é justamente a parte que decide o que o robô vai executar.
"""

from __future__ import annotations

import subprocess
import unittest
from unittest import mock

from wifi import rede

IFACE = "wlan0"


def _resposta(codigo: int = 0, saida: str = "", erro: str = ""):
    return subprocess.CompletedProcess(args=["nmcli"], returncode=codigo, stdout=saida, stderr=erro)


class TestComandoMalformado(unittest.TestCase):
    """A promessa do módulo: nunca levantar para fora, venha o que vier."""

    def test_nao_e_dicionario(self):
        self.assertEqual(rede.processar("conectar", IFACE)["erro"], "formato_invalido")

    def test_acao_desconhecida(self):
        resposta = rede.processar({"acao": "formatar_o_robo"}, IFACE)
        self.assertFalse(resposta["ok"])
        self.assertEqual(resposta["erro"], "acao_desconhecida")

    def test_conectar_sem_ssid(self):
        resposta = rede.processar({"acao": "conectar"}, IFACE)
        self.assertEqual(resposta["erro"], "ssid_obrigatorio")

    def test_ssid_que_nao_e_texto(self):
        resposta = rede.processar({"acao": "conectar", "ssid": 42}, IFACE)
        self.assertEqual(resposta["erro"], "ssid_obrigatorio")

    def test_senha_que_nao_e_texto_nao_chega_ao_subprocess(self):
        """Uma senha em lista virava `TypeError` dentro do `subprocess`.

        `TypeError` não é `ErroRede`, então escapava do `try` de `processar` e
        subia — pelo caminho de um comando que qualquer celular em alcance BLE
        consegue mandar.
        """
        with mock.patch.object(rede, "_run_nmcli") as nmcli:
            resposta = rede.processar(
                {"acao": "conectar", "ssid": "Rede", "senha": ["a", "b"]}, IFACE
            )
        self.assertEqual(resposta["erro"], "senha_invalida")
        nmcli.assert_not_called()

    def test_ssid_comecando_com_hifen_e_recusado(self):
        """`nmcli` leria "--help" como opção, não como nome de rede."""
        with mock.patch.object(rede, "_run_nmcli") as nmcli:
            resposta = rede.processar({"acao": "conectar", "ssid": "--help"}, IFACE)
        self.assertEqual(resposta["erro"], "ssid_invalido")
        nmcli.assert_not_called()


class TestConectar(unittest.TestCase):
    def test_senha_vai_como_argumento_separado(self):
        """Nada de shell: a senha é um item da lista, não texto interpolado."""
        chamadas = []

        def falso(args, timeout=45.0):
            chamadas.append(args)
            if args[0] == "-w":
                return _resposta()
            return _resposta(saida="GENERAL.CONNECTION:MinhaRede\nIP4.ADDRESS[1]:192.168.0.9/24\n")

        with mock.patch.object(rede, "_run_nmcli", side_effect=falso):
            resposta = rede.processar(
                {"acao": "conectar", "ssid": "Rede da Casa", "senha": "senha; rm -rf /"}, IFACE
            )

        self.assertTrue(resposta["ok"])
        self.assertIn("senha; rm -rf /", chamadas[0])
        self.assertIn("Rede da Casa", chamadas[0])

    def test_password_em_ingles_tambem_vale(self):
        with mock.patch.object(rede, "_run_nmcli", return_value=_resposta()) as nmcli:
            rede.processar({"acao": "conectar", "ssid": "Rede", "password": "abc"}, IFACE)
        self.assertIn("abc", nmcli.call_args_list[0].args[0])

    def test_rede_aberta_nao_manda_password(self):
        with mock.patch.object(rede, "_run_nmcli", return_value=_resposta()) as nmcli:
            rede.processar({"acao": "conectar", "ssid": "Aberta"}, IFACE)
        self.assertNotIn("password", nmcli.call_args_list[0].args[0])

    def test_falha_do_nmcli_vira_resposta_e_nao_excecao(self):
        with mock.patch.object(
            rede, "_run_nmcli", return_value=_resposta(codigo=4, erro="Secrets were required")
        ):
            resposta = rede.processar({"acao": "conectar", "ssid": "Rede", "senha": "x"}, IFACE)
        self.assertFalse(resposta["ok"])
        self.assertEqual(resposta["erro"], "falha_rede")
        self.assertIn("Secrets", resposta["detalhe"])

    def test_nmcli_ausente_vira_resposta_e_nao_excecao(self):
        with mock.patch.object(rede, "_run_nmcli", side_effect=rede.ErroRede("nmcli não encontrado")):
            resposta = rede.processar({"acao": "status"}, IFACE)
        self.assertFalse(resposta["ok"])
        self.assertEqual(resposta["erro"], "falha_rede")


class TestListarEStatus(unittest.TestCase):
    def test_ssid_com_dois_pontos_sobrevive_ao_modo_terse(self):
        """O `nmcli -t` escapa `:` com `\\`; um split ingênuo partiria o nome."""
        saida = "Casa\\: 2G:72:WPA2\nVizinho:41:WPA2\n"
        with mock.patch.object(rede, "_run_nmcli", return_value=_resposta(saida=saida)):
            resposta = rede.processar({"acao": "listar"}, IFACE)
        nomes = [r["ssid"] for r in resposta["redes"]]
        self.assertIn("Casa: 2G", nomes)

    def test_redes_ocultas_e_repetidas_ficam_de_fora(self):
        saida = ":90:WPA2\nCasa:72:WPA2\nCasa:60:WPA2\n"
        with mock.patch.object(rede, "_run_nmcli", return_value=_resposta(saida=saida)):
            resposta = rede.processar({"acao": "listar"}, IFACE)
        self.assertEqual([r["ssid"] for r in resposta["redes"]], ["Casa"])

    def test_a_mais_forte_vem_primeiro(self):
        saida = "Fraca:20:WPA2\nForte:88:WPA2\n"
        with mock.patch.object(rede, "_run_nmcli", return_value=_resposta(saida=saida)):
            resposta = rede.processar({"acao": "listar"}, IFACE)
        self.assertEqual(resposta["redes"][0]["ssid"], "Forte")

    def test_status_desconectado(self):
        saida = "GENERAL.CONNECTION:--\n"
        with mock.patch.object(rede, "_run_nmcli", return_value=_resposta(saida=saida)):
            resposta = rede.processar({"acao": "status"}, IFACE)
        self.assertFalse(resposta["conectado"])
        self.assertIsNone(resposta["ssid"])

    def test_status_conectado_traz_ip_sem_a_mascara(self):
        saida = "GENERAL.CONNECTION:Casa\nIP4.ADDRESS[1]:192.168.0.42/24\n"
        with mock.patch.object(rede, "_run_nmcli", return_value=_resposta(saida=saida)):
            resposta = rede.processar({"acao": "status"}, IFACE)
        self.assertTrue(resposta["conectado"])
        self.assertEqual(resposta["ip"], "192.168.0.42")


if __name__ == "__main__":
    unittest.main()
