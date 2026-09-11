"""A leitura da saúde do Pi, exercitada com dados de verdade — sem um Pi.

A máquina de quem escreve isto não tem `vcgencmd` nem os arquivos de `/proc`
de um Raspberry. Estes testes alimentam o texto cru que cada fonte produziria e
conferem a conta — porque um bit de `throttled` lido ao contrário, ou uma
porcentagem invertida, só apareceria no robô, tarde, no histórico.
"""

from __future__ import annotations

import unittest

from telemetria import coletor


class TestTemperatura(unittest.TestCase):
    def test_milesimos_viram_graus(self):
        self.assertEqual(coletor.parse_temperatura("54321\n"), 54.3)

    def test_vazio_e_lixo_nao_mentem_zero(self):
        self.assertIsNone(coletor.parse_temperatura(""))
        self.assertIsNone(coletor.parse_temperatura("N/A"))


class TestThrottled(unittest.TestCase):
    def test_zero_e_tudo_ok(self):
        r = coletor.parse_throttled("throttled=0x0")
        self.assertTrue(r["ok"])
        self.assertFalse(r["subtensao_agora"])
        self.assertFalse(r["throttling_ja_ocorreu"])
        self.assertEqual(r["hex"], "0x0")

    def test_subtensao_agora_e_ja_ocorreu(self):
        # bit 0 (agora) + bit 16 (já ocorreu) = 0x10001
        r = coletor.parse_throttled("throttled=0x10001")
        self.assertFalse(r["ok"])
        self.assertTrue(r["subtensao_agora"])
        self.assertTrue(r["subtensao_ja_ocorreu"])
        self.assertFalse(r["throttling_agora"])

    def test_limite_termico_ja_ocorreu_sem_estar_agora(self):
        # bit 19 apenas (limite térmico já ocorreu) = 0x80000
        r = coletor.parse_throttled("throttled=0x80000")
        self.assertFalse(r["ok"])
        self.assertTrue(r["limite_termico_ja_ocorreu"])
        self.assertFalse(r["limite_termico_agora"])

    def test_aceita_sem_prefixo_e_sem_igual(self):
        self.assertEqual(coletor.parse_throttled("0x0")["hex"], "0x0")

    def test_lixo_vira_none(self):
        self.assertIsNone(coletor.parse_throttled(""))
        self.assertIsNone(coletor.parse_throttled("throttled=xyz"))


class TestCarga(unittest.TestCase):
    def test_tres_medias(self):
        r = coletor.parse_loadavg("0.52 0.41 0.38 1/234 5678")
        self.assertEqual(r, {"1m": 0.52, "5m": 0.41, "15m": 0.38})

    def test_curto_demais_vira_none(self):
        self.assertIsNone(coletor.parse_loadavg("0.5"))


class TestMemoria(unittest.TestCase):
    def test_usa_available_nao_free(self):
        raw = "MemTotal:        8000000 kB\nMemFree:          100000 kB\nMemAvailable:    6000000 kB\n"
        r = coletor.parse_meminfo(raw)
        # available, não free: usado = 8000000 - 6000000 = 2000000 kB = 25%
        self.assertEqual(r["uso_pct"], 25.0)
        self.assertEqual(r["total_mb"], round(8000000 / 1024))
        self.assertEqual(r["disponivel_mb"], round(6000000 / 1024))

    def test_sem_os_campos_vira_none(self):
        self.assertIsNone(coletor.parse_meminfo("Foo: 1 kB\n"))


class TestUptime(unittest.TestCase):
    def test_primeiro_campo(self):
        self.assertEqual(coletor.parse_uptime("123456.78 98765.43"), 123456.8)

    def test_vazio(self):
        self.assertIsNone(coletor.parse_uptime(""))


class TestDisco(unittest.TestCase):
    def test_uso_em_gb_e_pct(self):
        # 100 GB total, 25 GB livres -> 75% usado
        r = coletor.uso_disco(100 * 1024**3, 25 * 1024**3)
        self.assertEqual(r["total_gb"], 100.0)
        self.assertEqual(r["livre_gb"], 25.0)
        self.assertEqual(r["uso_pct"], 75.0)

    def test_total_zero_vira_none(self):
        self.assertIsNone(coletor.uso_disco(0, 0))


class TestCpu(unittest.TestCase):
    def test_delta_de_duas_leituras(self):
        # entre as duas: ocupado +50, total +100 -> 50%
        antes = coletor.total_e_ocupado_do_proc_stat("cpu  100 0 0 100 0 0 0")
        # ocupado(200) idle(150) total(350)? montemos explicitamente:
        # user=150 idle=150 -> ocupado=150 total=300; delta ocupado=50 total=100
        depois = (150, 300)
        self.assertEqual(antes, (100, 200))  # ocupado=100, total=200
        self.assertEqual(coletor.cpu_uso_pct(antes, depois), 50.0)

    def test_sem_intervalo_vira_none(self):
        self.assertIsNone(coletor.cpu_uso_pct((100, 200), (100, 200)))

    def test_proc_stat_separa_idle_e_iowait(self):
        # cpu user=10 nice=0 system=5 idle=80 iowait=5 -> total=100, ocupado=15
        r = coletor.total_e_ocupado_do_proc_stat("cpu  10 0 5 80 5 0 0 0\ncpu0 ...")
        self.assertEqual(r, (15, 100))

    def test_linha_invalida_vira_none(self):
        self.assertIsNone(coletor.total_e_ocupado_do_proc_stat("intr 123 456"))


class TestWifi(unittest.TestCase):
    RAW = (
        "Inter-| sta-|   Quality        |   Discarded packets\n"
        " face | tus | link level noise |  nwid  crypt   frag\n"
        " wlan0: 0000   58.  -52.  -256        0      0      0\n"
    )

    def test_link_e_sinal(self):
        r = coletor.parse_wireless(self.RAW, "wlan0")
        self.assertEqual(r["link"], 58)
        self.assertEqual(r["sinal_dbm"], -52)
        self.assertEqual(r["interface"], "wlan0")

    def test_interface_ausente_vira_none(self):
        # cabo, ou Wi-Fi caído: a interface não está no arquivo.
        self.assertIsNone(coletor.parse_wireless(self.RAW, "wlan1"))


if __name__ == "__main__":
    unittest.main()
