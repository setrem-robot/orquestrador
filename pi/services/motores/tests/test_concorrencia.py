"""Duas threads mexem no `ServicoMotores`, e elas não podem se atropelar.

`receber()` é chamado pela thread de rede do paho, quando uma mensagem chega do
broker. `tick()` é chamado pelo laço de `main()`, vinte vezes por segundo. Os
dois avançam a mesma rampa e escrevem no mesmo acionamento.

A sequência que assusta, sem exclusão mútua: chega uma parada de emergência,
`receber()` zera a rampa e manda parar — e o `tick()` que já estava no meio do
caminho aplica, logo depois, a velocidade que tinha lido antes da parada. O robô
recebe "pare" e continua andando.

Testar isso com `sleep` e sorte daria um teste que passa quase sempre, que é
pior que nenhum. Em vez disso, os testes abaixo verificam a propriedade que
importa e é determinística: **enquanto uma das operações está aplicando
velocidade no hardware, nenhuma outra thread consegue entrar.**
"""

from __future__ import annotations

import threading
import unittest

from motores.acionamento import AcionamentoSimulado
from motores.main import ServicoMotores
from motores.vigia import Vigia


class AcionamentoEspiao(AcionamentoSimulado):
    """Enquanto aplica, tenta entrar no serviço por outra thread.

    Se conseguir, é porque não havia cadeado nenhum: o `aplicar` acontece dentro
    da região crítica, e nesse instante mais ninguém pode estar lá dentro.
    """

    def __init__(self) -> None:
        super().__init__()
        self.servico: ServicoMotores | None = None
        self.invasoes = 0
        self.aplicados: list[tuple[float, float]] = []

    def aplicar(self, velocidades) -> None:
        super().aplicar(velocidades)
        self.aplicados.append((velocidades.esquerda, velocidades.direita))
        if self.servico is None:
            return

        def invadir() -> None:
            # `acquire(blocking=False)` de outra thread: com o cadeado tomado,
            # falha; sem cadeado, entra na hora.
            if self.servico.cadeado.acquire(blocking=False):
                self.invasoes += 1
                self.servico.cadeado.release()

        t = threading.Thread(target=invadir)
        t.start()
        t.join(timeout=2.0)


def criarServico() -> tuple[ServicoMotores, AcionamentoEspiao]:
    espiao = AcionamentoEspiao()
    servico = ServicoMotores(espiao, Vigia(1.0), aceleracao=0.0)
    espiao.servico = servico
    return servico, espiao


class TestExclusaoMutua(unittest.TestCase):
    def testNinguemEntraEnquantoOTickAplica(self):
        servico, espiao = criarServico()
        servico.tick(0.05, 1.0)
        self.assertEqual(espiao.invasoes, 0, "outra thread entrou durante o tick")

    def testNinguemEntraEnquantoUmComandoEAplicado(self):
        servico, espiao = criarServico()
        servico.receber({"acao": "parar"}, 1.0)
        self.assertEqual(espiao.invasoes, 0, "outra thread entrou durante o comando")

    def testNinguemEntraDuranteAParadaDeEmergencia(self):
        servico, espiao = criarServico()
        servico.paradaDeEmergencia("o teste mandou")
        self.assertEqual(espiao.invasoes, 0, "outra thread entrou durante a parada")

    def testNinguemEntraDuranteOEncerramento(self):
        servico, espiao = criarServico()
        servico.encerrar()
        self.assertEqual(espiao.invasoes, 0, "outra thread entrou durante o encerramento")


class TestOCadeadoNaoTrava(unittest.TestCase):
    """Reentrância: `tick()` chama `parada_de_emergencia()`, que também tranca."""

    def testOVigiaExpirandoDentroDoTickNaoTrava(self):
        servico, espiao = criarServico()
        servico.receber({"acao": "frente", "velocidade": 80}, 1.0)
        # Um segundo e pouco depois, sem repetição: o vigia manda parar, e ele
        # faz isso de dentro do `tick`. Com um `Lock` simples no lugar de um
        # `RLock`, esta linha travaria o serviço para sempre.
        servico.tick(0.05, 3.0)
        self.assertEqual(espiao.aplicados[-1], (0.0, 0.0))

    def testOLacoContinuaVivoDepoisDisso(self):
        servico, espiao = criarServico()
        servico.receber({"acao": "frente", "velocidade": 80}, 1.0)
        servico.tick(0.05, 3.0)
        servico.receber({"acao": "frente", "velocidade": 40}, 4.0)
        servico.tick(0.05, 4.05)
        self.assertGreater(espiao.aplicados[-1][0], 0.0)


class TestMuitasThreadsAoMesmoTempo(unittest.TestCase):
    def testComandosETicksEmParaleloNaoQuebramNada(self):
        """Não afere ordem — afere que nada explode e que a parada é a última palavra."""
        espiao = AcionamentoEspiao()
        servico = ServicoMotores(espiao, Vigia(1.0), aceleracao=0.0)
        erros: list[BaseException] = []

        def mandar(n: int) -> None:
            try:
                for i in range(50):
                    servico.receber({"acao": "frente", "velocidade": 50 + n}, 1.0 + i * 0.001)
            except BaseException as exc:
                erros.append(exc)

        def tiquetaquear() -> None:
            try:
                for i in range(50):
                    servico.tick(0.01, 1.0 + i * 0.001)
            except BaseException as exc:
                erros.append(exc)

        threads = [threading.Thread(target=mandar, args=(n,)) for n in range(3)]
        threads += [threading.Thread(target=tiquetaquear) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)

        self.assertEqual(erros, [])
        servico.paradaDeEmergencia("fim do teste")
        self.assertEqual(espiao.aplicados[-1], (0.0, 0.0))


if __name__ == "__main__":
    unittest.main()
