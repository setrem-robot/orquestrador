"""Testes do serviço de motores de ponta a ponta, sem hardware e sem broker.

`AcionamentoSimulado` faz o papel dos motores e `ServicoMotores` recebe o
instante de quem o chama — é o que torna possível verificar aqui o que antes só
se via com o robô montado: que a parada de emergência não espera a rampa, que o
silêncio do controle para o robô, e que um comando torto não derruba o serviço.
"""

from __future__ import annotations

import unittest

from motores.acionamento import AcionamentoSimulado, passosPorSegundo
from motores.cinematica import Velocidades
from motores.main import ServicoMotores
from motores.vigia import Vigia


def montar(*, aceleracao: float = 0.0, timeoutS: float = 1.0):
    """Serviço pronto para o teste, com os motores simulados.

    A rampa nasce desligada porque a maior parte dos testes é sobre decisão, e
    não sobre aceleração — quem testa a rampa é `test_cinematica.py`. Os que
    precisam dela a ligam explicitamente.
    """
    acionamento = AcionamentoSimulado()
    publicado: list[dict] = []
    servico = ServicoMotores(
        acionamento,
        Vigia(timeoutS),
        aceleracao=aceleracao,
        timeoutS=timeoutS,
        publicarStatus=publicado.append,
    )
    return servico, acionamento, publicado


class TestComandos(unittest.TestCase):
    def testFrenteChegaAosMotores(self):
        servico, motores, _ = montar()
        servico.receber({"acao": "frente", "velocidade": 100}, agora=1.0)
        servico.tick(0.05, agora=1.05)
        self.assertEqual(motores.atual, Velocidades(1.0, 1.0))

    def testModoContinuoChegaAosMotores(self):
        servico, motores, _ = montar()
        servico.receber({"acao": "mover", "linear": 0.5, "angular": 0.25}, agora=1.0)
        servico.tick(0.05, agora=1.05)
        self.assertAlmostEqual(motores.atual.esquerda, 0.75)
        self.assertAlmostEqual(motores.atual.direita, 0.25)

    def testAcaoDesconhecidaEIgnoradaSemDerrubar(self):
        servico, motores, publicado = montar()
        servico.receber({"acao": "decolar"}, agora=1.0)
        self.assertEqual(motores.historico, [])
        self.assertEqual(publicado, [])

    def testComandoComLixoNoLugarDoNumeroNaoDerruba(self):
        # O app é não-confiável, e o serviço que segura os motores é o último
        # lugar do sistema que pode morrer por causa de um campo mal preenchido.
        servico, motores, _ = montar()
        servico.receber({"acao": "frente", "velocidade": "rápido"}, agora=1.0)
        self.assertEqual(motores.historico, [])

    def testStatusRepetidoNaoERepublicado(self):
        # O app repete o comando três vezes por segundo enquanto o dedo está no
        # botão; publicar cada repetição encheria o tópico retido e a telemetria.
        servico, _, publicado = montar()
        for instante in (1.0, 1.3, 1.6):
            servico.receber({"acao": "frente", "velocidade": 60}, agora=instante)
        self.assertEqual(len(publicado), 1)

    def testStatusLevaOsDoisLadosEAVelocidadeAntiga(self):
        servico, _, publicado = montar()
        servico.receber({"acao": "frente", "velocidade": 80}, agora=1.0)
        self.assertEqual(publicado[0]["velocidade"], 80)
        self.assertEqual(publicado[0]["esquerda"], 0.8)
        self.assertEqual(publicado[0]["direita"], 0.8)


class TestParadas(unittest.TestCase):
    def testPararNaoEsperaARampa(self):
        # Vigia folgado de propósito: aqui o que se testa é a rampa, e um
        # timeout curto pararia o robô pelo outro motivo antes da verificação.
        servico, motores, _ = montar(aceleracao=0.5, timeoutS=30.0)
        servico.receber({"acao": "frente", "velocidade": 100}, agora=1.0)
        servico.tick(1.0, agora=2.0)
        self.assertFalse(motores.atual.parado)

        servico.receber({"acao": "parar"}, agora=2.0)
        self.assertTrue(motores.atual.parado)

    def testSilencioDoControleParaORobo(self):
        # A terceira camada de segurança do contrato: o app repete o comando, e
        # a ausência dele passa a ser um sinal em si.
        servico, motores, publicado = montar(timeoutS=1.0)
        servico.receber({"acao": "frente", "velocidade": 100}, agora=1.0)
        servico.tick(0.05, agora=1.5)
        self.assertFalse(motores.atual.parado)

        servico.tick(0.05, agora=2.5)
        self.assertTrue(motores.atual.parado)
        self.assertIn("motivo", publicado[-1])

    def testOSilencioParaUmaVezSo(self):
        servico, _, publicado = montar(timeoutS=1.0)
        servico.receber({"acao": "frente", "velocidade": 100}, agora=1.0)
        servico.tick(0.05, agora=2.5)
        antes = len(publicado)
        for instante in (2.55, 2.60, 2.65):
            servico.tick(0.05, agora=instante)
        self.assertEqual(len(publicado), antes)

    def testMoverComEixosZeradosTambemDesarmaOVigia(self):
        # `{"acao":"mover"}` com os eixos em zero é uma parada, ainda que a
        # palavra "parar" não apareça em lugar nenhum do comando.
        servico, motores, _ = montar(timeoutS=1.0)
        servico.receber({"acao": "mover", "linear": 0.0, "angular": 0.0}, agora=1.0)
        servico.tick(0.05, agora=5.0)
        self.assertTrue(motores.atual.parado)

    def testEncerrarParaESoltaOsMotores(self):
        servico, motores, _ = montar()
        servico.receber({"acao": "frente", "velocidade": 100}, agora=1.0)
        servico.encerrar()
        self.assertTrue(motores.atual.parado)
        self.assertTrue(motores.fechado)


class TestPulso(unittest.TestCase):
    """A conversão de velocidade em frequência do pulso STEP."""

    def testParadoNaoPulsa(self):
        self.assertEqual(passosPorSegundo(0.0), 0.0)

    def testVelocidadeMaximaDaOTopoDaFaixa(self):
        self.assertEqual(passosPorSegundo(1.0), 1000.0)

    def testARePulsaIgualAIda(self):
        # O sentido é do pino DIR; a frequência só conhece intensidade.
        self.assertEqual(passosPorSegundo(-0.5), passosPorSegundo(0.5))


if __name__ == "__main__":
    unittest.main()
