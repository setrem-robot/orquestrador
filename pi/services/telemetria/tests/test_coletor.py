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
    def testMilesimosViramGraus(self):
        self.assertEqual(coletor.parseTemperatura("54321\n"), 54.3)

    def testVazioELixoNaoMentemZero(self):
        self.assertIsNone(coletor.parseTemperatura(""))
        self.assertIsNone(coletor.parseTemperatura("N/A"))


class TestThrottled(unittest.TestCase):
    def testZeroETudoOk(self):
        r = coletor.parseThrottled("throttled=0x0")
        self.assertTrue(r["ok"])
        self.assertFalse(r["subtensao_agora"])
        self.assertFalse(r["throttling_ja_ocorreu"])
        self.assertEqual(r["hex"], "0x0")

    def testSubtensaoAgoraEJaOcorreu(self):
        # bit 0 (agora) + bit 16 (já ocorreu) = 0x10001
        r = coletor.parseThrottled("throttled=0x10001")
        self.assertFalse(r["ok"])
        self.assertTrue(r["subtensao_agora"])
        self.assertTrue(r["subtensao_ja_ocorreu"])
        self.assertFalse(r["throttling_agora"])

    def testLimiteTermicoJaOcorreuSemEstarAgora(self):
        # bit 19 apenas (limite térmico já ocorreu) = 0x80000
        r = coletor.parseThrottled("throttled=0x80000")
        self.assertFalse(r["ok"])
        self.assertTrue(r["limite_termico_ja_ocorreu"])
        self.assertFalse(r["limite_termico_agora"])

    def testAceitaSemPrefixoESemIgual(self):
        self.assertEqual(coletor.parseThrottled("0x0")["hex"], "0x0")

    def testLixoViraNone(self):
        self.assertIsNone(coletor.parseThrottled(""))
        self.assertIsNone(coletor.parseThrottled("throttled=xyz"))


class TestCarga(unittest.TestCase):
    def testTresMedias(self):
        r = coletor.parseLoadavg("0.52 0.41 0.38 1/234 5678")
        self.assertEqual(r, {"1m": 0.52, "5m": 0.41, "15m": 0.38})

    def testCurtoDemaisViraNone(self):
        self.assertIsNone(coletor.parseLoadavg("0.5"))


class TestMemoria(unittest.TestCase):
    def testUsaAvailableNaoFree(self):
        raw = "MemTotal:        8000000 kB\nMemFree:          100000 kB\nMemAvailable:    6000000 kB\n"
        r = coletor.parseMeminfo(raw)
        # available, não free: usado = 8000000 - 6000000 = 2000000 kB = 25%
        self.assertEqual(r["uso_pct"], 25.0)
        self.assertEqual(r["total_mb"], round(8000000 / 1024))
        self.assertEqual(r["disponivel_mb"], round(6000000 / 1024))
        # sem linhas de swap, o bloco não inventa os campos
        self.assertNotIn("swap_total_mb", r)

    def testSwapQuandoPresente(self):
        raw = (
            "MemTotal:  8000000 kB\nMemAvailable: 6000000 kB\n"
            "SwapTotal: 2000000 kB\nSwapFree:  1500000 kB\n"
        )
        r = coletor.parseMeminfo(raw)
        self.assertEqual(r["swap_total_mb"], round(2000000 / 1024))
        # usado = total - livre = 500000 kB
        self.assertEqual(r["swap_usado_mb"], round(500000 / 1024))

    def testSemOsCamposViraNone(self):
        self.assertIsNone(coletor.parseMeminfo("Foo: 1 kB\n"))


class TestProcessos(unittest.TestCase):
    def testRodandoETotal(self):
        self.assertEqual(
            coletor.parseProcessos("0.52 0.41 0.38 2/234 5678"),
            {"rodando": 2, "total": 234},
        )

    def testSemOCampoViraNone(self):
        self.assertIsNone(coletor.parseProcessos("0.5 0.4 0.3"))


class TestFrequenciaEVoltagem(unittest.TestCase):
    def testKhzViraMhz(self):
        self.assertEqual(coletor.parseFreqKhz("1500000\n"), 1500)

    def testFreqLixoViraNone(self):
        self.assertIsNone(coletor.parseFreqKhz(""))
        self.assertIsNone(coletor.parseFreqKhz("N/A"))

    def testVolts(self):
        self.assertEqual(coletor.parseVolts("volt=0.8563V"), 0.856)

    def testVoltsLixoViraNone(self):
        self.assertIsNone(coletor.parseVolts(""))
        self.assertIsNone(coletor.parseVolts("volt=xV"))


class TestRede(unittest.TestCase):
    RAW = (
        "Inter-|   Receive                    |  Transmit\n"
        " face |bytes    packets errs drop fifo frame compressed multicast|"
        "bytes    packets errs drop fifo colls carrier compressed\n"
        "    lo:  1000      10    0    0    0     0          0         0    "
        "1000      10    0    0    0     0       0          0\n"
        "  eth0: 2097152   500    0    0    0     0          0         0   "
        "1048576   400    0    0    0     0       0          0\n"
    )

    def testBytesPorInterfaceEmMb(self):
        r = coletor.parseRede(self.RAW)
        self.assertIn("eth0", r)
        self.assertEqual(r["eth0"]["rx_mb"], 2.0)  # 2 MiB
        self.assertEqual(r["eth0"]["tx_mb"], 1.0)  # 1 MiB

    def testLoopbackFora(self):
        self.assertNotIn("lo", coletor.parseRede(self.RAW))

    def testSemInterfaceViraNone(self):
        self.assertIsNone(coletor.parseRede("cabecalho\nsem dois pontos\n"))


class TestUptime(unittest.TestCase):
    def testPrimeiroCampo(self):
        self.assertEqual(coletor.parseUptime("123456.78 98765.43"), 123456.8)

    def testVazio(self):
        self.assertIsNone(coletor.parseUptime(""))


class TestDisco(unittest.TestCase):
    def testUsoEmGbEPct(self):
        # 100 GB total, 25 GB livres -> 75% usado
        r = coletor.usoDisco(100 * 1024**3, 25 * 1024**3)
        self.assertEqual(r["total_gb"], 100.0)
        self.assertEqual(r["livre_gb"], 25.0)
        self.assertEqual(r["uso_pct"], 75.0)

    def testTotalZeroViraNone(self):
        self.assertIsNone(coletor.usoDisco(0, 0))


class TestCpu(unittest.TestCase):
    def testDeltaDeDuasLeituras(self):
        # entre as duas: ocupado +50, total +100 -> 50%
        antes = coletor.totalEOcupadoDoProcStat("cpu  100 0 0 100 0 0 0")
        # ocupado(200) idle(150) total(350)? montemos explicitamente:
        # user=150 idle=150 -> ocupado=150 total=300; delta ocupado=50 total=100
        depois = (150, 300)
        self.assertEqual(antes, (100, 200))  # ocupado=100, total=200
        self.assertEqual(coletor.cpuUsoPct(antes, depois), 50.0)

    def testSemIntervaloViraNone(self):
        self.assertIsNone(coletor.cpuUsoPct((100, 200), (100, 200)))

    def testProcStatSeparaIdleEIowait(self):
        # cpu user=10 nice=0 system=5 idle=80 iowait=5 -> total=100, ocupado=15
        r = coletor.totalEOcupadoDoProcStat("cpu  10 0 5 80 5 0 0 0\ncpu0 ...")
        self.assertEqual(r, (15, 100))

    def testLinhaInvalidaViraNone(self):
        self.assertIsNone(coletor.totalEOcupadoDoProcStat("intr 123 456"))

    def testNucleosSeparaAgregadoECadaNucleo(self):
        raw = (
            "cpu  40 0 20 320 20 0 0 0\n"
            "cpu0 10 0 5 80 5 0 0 0\n"
            "cpu1 30 0 15 240 15 0 0 0\n"
            "intr 999\nctxt 12345\n"
        )
        r = coletor.nucleosDoProcStat(raw)
        self.assertEqual(set(r), {"cpu", "cpu0", "cpu1"})  # intr/ctxt fora
        self.assertEqual(r["cpu"], (60, 400))  # ocupado=tudo-idle-iowait
        self.assertEqual(r["cpu0"], (15, 100))

    def testNucleosVazioNaoQuebra(self):
        self.assertEqual(coletor.nucleosDoProcStat(""), {})


class TestWifi(unittest.TestCase):
    RAW = (
        "Inter-| sta-|   Quality        |   Discarded packets\n"
        " face | tus | link level noise |  nwid  crypt   frag\n"
        " wlan0: 0000   58.  -52.  -256        0      0      0\n"
    )

    def testLinkESinal(self):
        r = coletor.parseWireless(self.RAW, "wlan0")
        self.assertEqual(r["link"], 58)
        self.assertEqual(r["sinal_dbm"], -52)
        self.assertEqual(r["interface"], "wlan0")

    def testInterfaceAusenteViraNone(self):
        # cabo, ou Wi-Fi caído: a interface não está no arquivo.
        self.assertIsNone(coletor.parseWireless(self.RAW, "wlan1"))


if __name__ == "__main__":
    unittest.main()
