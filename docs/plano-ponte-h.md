# Estudo e plano — controlar motores por ponte H no Raspberry Pi

Objetivo: acionar os motores do robô por **ponte H**, controlada em Python no
Raspberry Pi 5, encaixando no serviço `motores` que já existe.

> [!IMPORTANT]
> **Antes de tudo: o motor é de corrente contínua (DC) ou de passo?**
> Hoje o código (`motores/acionamento.py`) aciona **motores de passo NEMA 23 com
> driver TMC2209** (STEP/DIR + pulso por PWM). Uma **ponte H** serve para **motor
> DC (escovado)** — direção por dois pinos e velocidade por PWM. **São coisas
> diferentes:** não se troca um driver de passo por uma ponte H simples mantendo
> o mesmo motor. Este plano assume que você vai usar **motores DC** (é o que faz
> sentido com "ponte H"). Se os motores continuarem de passo, o caminho é
> driver de passo, não ponte H — me avise e eu ajusto. O resto do documento
> segue no cenário **DC + ponte H**.

---

## 1. Como uma ponte H controla um motor DC

Uma ponte H liga o motor à fonte nos **dois sentidos**. Por motor, o controle é:

- **Direção:** dois sinais lógicos (IN1/IN2). `10` gira num sentido, `01` no
  outro, `00` = livre (coast), `11` = freio (curto nos terminais).
- **Velocidade:** um sinal **PWM** (ENA) — a razão liga/desliga define quanto de
  tensão média chega ao motor. 0% parado, 100% velocidade máxima.

Ou seja, cada motor precisa de **3 fios de controle** do Pi: `IN1`, `IN2`, `ENA`.
Dois motores = 6 GPIOs.

**Frear × dar coast:** para o robô, "parar" pode ser cortar o PWM (coast, o robô
desliza) ou frear (IN1=IN2=1, para mais seco). O `parar()` do acionamento vai
cortar o PWM por padrão; freio é opção.

---

## 2. Escolher a ponte H

O que decide é a **corrente** que os motores puxam (principalmente na partida e
em stall). Opções comuns:

| Módulo | Corrente | Lógica | Observações |
|---|---|---|---|
| **L298N** | ~2 A/canal (pico 3 A) | 5 V (aceita 3,3 V do Pi nas entradas) | Clássico e barato. **Perde ~2 V** e esquenta (BJT); precisa de dissipador. OK para motores pequenos/educacional. |
| **TB6612FNG** | ~1,2 A/canal (pico 3,2 A) | **3,3 V nativo** | MOSFET, eficiente, pouco calor. Ótimo para robô pequeno. Combina melhor com o Pi que o L298N. |
| **DRV8833 / DRV8871** | 1,5–3,6 A | 3,3 V | MOSFET, eficiente, compacto. |
| **BTS7960 (IBT-2)** | até ~43 A | 3,3 V | Para motores grandes/alta corrente. PWM por canal (PWM+dir). |
| **Cytron MDD10 / MDDS** | 10–30 A | 3,3–5 V | Robusto, feito para robótica, fácil de ligar. |

**Recomendação:**
- Motores pequenos (gear motor de hobby): **TB6612FNG** (eficiente e 3,3 V) ou,
  se quiser o mais fácil de achar, **L298N** (com dissipador).
- Motores grandes (o robô é pesado, herdou NEMA 23): **BTS7960** ou **Cytron** —
  o L298N não dá conta da corrente.

**Meça o motor antes de comprar:** corrente de stall na tensão de trabalho. A
ponte H tem que aguentar o stall, não só o consumo em movimento.

### Regras elétricas que valem para qualquer módulo
- **Fonte de motor separada** (bateria), **não** alimente o motor pelo Pi.
- **Terra comum obrigatório:** GND do Pi ↔ GND da ponte ↔ GND da fonte. Sem
  isso o Pi e a ponte não "concordam" no nível lógico.
- Entradas de lógica: GPIO do Pi é **3,3 V**. TB6612/DRV/BTS/Cytron são 3,3 V —
  ligam direto. O L298N é 5 V, mas as entradas costumam disparar com 3,3 V;
  funciona na prática. **Nunca** ligue uma saída de 5 V da ponte num GPIO do Pi.
- Os módulos já trazem os **diodos de roda-livre** (flyback). Se montar ponte
  discreta, eles são obrigatórios.

---

## 3. Controle em Python no Pi 5 (gpiozero)

O projeto **já usa `gpiozero` + `lgpio`** (é o que gera o PWM do passo hoje),
então não entra dependência nova. O gpiozero tem a classe pronta:

```python
from gpiozero import Motor

# L298N: IN1, IN2 = direção; enable = ENA (PWM). pwm=True usa PWM no enable.
esquerda = Motor(forward=20, backward=21, enable=16, pwm=True)
direita  = Motor(forward=19, backward=26, enable=13, pwm=True)

esquerda.value = 0.7    # 70% para a frente  (aceita -1.0 .. 1.0)
direita.value  = -0.5   # 50% para trás
esquerda.stop()         # coast (PWM = 0)
```

O `Motor.value` vai de **-1 (ré total) a 1 (frente total)** — exatamente a faixa
que a nossa `cinematica.Velocidades` já produz por lado. O mapeamento é direto:
`motor.value = velocidades.esquerda`. Não há conta a fazer.

> No Pi 5 o gpiozero usa o backend **lgpio** automaticamente; o PWM aqui é por
> software (via lgpio), suficiente para velocidade de motor DC — não precisa de
> pino de hardware-PWM específico.

Para drivers PWM+direção (BTS7960, ou um lado do TB6612), existe
`PhaseEnableMotor(phase_pin, enable_pin)`. E há `Robot(left=Motor(...),
right=Motor(...))` que junta os dois — mas aqui a gente encaixa na ABC que já
temos (abaixo), então usamos `Motor` por roda.

---

## 4. Onde isso encaixa no código (a parte elegante)

A arquitetura de `motores/` **já foi feita para essa troca**:

- **`cinematica.py`** — puro, reaproveitado **inteiro**. Comando → `Velocidades`
  (esquerda/direita, -1..1) + a `Rampa` de aceleração. Não muda nada.
- **`acionamento.py`** — tem a ABC `Acionamento` (`aplicar`, `parar`, `fechar`).
  Hoje com `AcionamentoStepper` e `AcionamentoSimulado`. **Adicionamos uma
  terceira: `AcionamentoPonteH`.**
- **`main.py` / `vigia.py`** — MQTT, laço e watchdog. **Não mudam.**

Esboço da nova classe (mesma ABC, `Motor` do gpiozero por roda):

```python
class AcionamentoPonteH(Acionamento):
    """Dois motores DC por ponte H (ex.: L298N/TB6612), velocidade por PWM."""

    def __init__(self, *, invertidoEsquerda=False, invertidoDireita=False):
        from gpiozero import Motor  # lazy: módulo carrega sem GPIO
        self.invEsq, self.invDir = invertidoEsquerda, invertidoDireita
        self.esq = Motor(forward=ESQ_IN1, backward=ESQ_IN2, enable=ESQ_ENA, pwm=True)
        self.dir = Motor(forward=DIR_IN1, backward=DIR_IN2, enable=DIR_ENA, pwm=True)
        self.aplicado = Velocidades()

    def aplicar(self, velocidades: Velocidades) -> None:
        if velocidades == self.aplicado:
            return                      # idempotente: o laço chama muito
        self.aplicado = velocidades
        if velocidades.parado:
            self.parar(); return
        ef = velocidades.invertendo(self.invEsq, self.invDir)
        self.esq.value = max(-1.0, min(1.0, ef.esquerda))
        self.dir.value = max(-1.0, min(1.0, ef.direita))

    def parar(self) -> None:
        self.esq.stop(); self.dir.stop()   # PWM = 0 (coast)
        self.aplicado = Velocidades()

    def fechar(self) -> None:
        for m in (self.esq, self.dir):
            try: m.close()
            except Exception: pass
```

E uma linha em `criarAcionamento` para selecioná-la por env:

```python
if escolha in ("ponteh", "ponte-h", "dc"):
    return AcionamentoPonteH(invertidoEsquerda=..., invertidoDireita=...)
```

Selecionado por `MOTORES_BACKEND=ponteh` (o mesmo mecanismo do `simulado`).

---

## 5. Ligação proposta (pinos)

Reaproveitando os GPIOs que eram do driver de passo (ficam livres ao trocar de
motor) — **não colidem** com o Bluetooth (GPIO14/15) nem com o GPS (GPIO4/5 ou
8/9, ver `setup-gps.md`):

| Sinal | GPIO (BCM) | Pino físico |
|---|---|---|
| Esq IN1 | 20 | 38 |
| Esq IN2 | 21 | 40 |
| Esq ENA (PWM) | 16 | 36 |
| Dir IN1 | 19 | 35 |
| Dir IN2 | 26 | 37 |
| Dir ENB (PWM) | 13 | 33 |
| GND lógico | — | 39 (ou qualquer GND) |

Alimentação: bateria → VMOT/12V da ponte; **GND comum** Pi ↔ ponte ↔ bateria.
(Confirme os pinos ao montar; ajusto as constantes no código conforme a placa.)

---

## 6. Segurança

- **Watchdog já existe:** `vigia.py` manda parar se passar ~1 s sem comando
  repetido. Vale para DC também — um motor DC em runaway é pior que um de passo.
  O `AcionamentoPonteH.parar()` corta o PWM.
- **Nasce parado:** os `Motor` começam em 0; nada de partir sozinho no boot.
- **Teste com o robô suspenso** (rodas no ar) antes de pôr no chão.
- **Limite de corrente / fusível** na linha da bateria; a ponte tem que aguentar
  o stall.

---

## 7. Plano de implementação (passos)

1. **Confirmar o motor** (DC?) e **medir a corrente de stall** → escolher a ponte H.
2. Comprar/receber a ponte H e a fonte de motor.
3. **Código primeiro, sem hardware:** implementar `AcionamentoPonteH` e o
   `MOTORES_BACKEND=ponteh`. Rodar tudo com `AcionamentoSimulado` e os testes de
   `cinematica`/`vigia` (que já existem) para provar a lógica na mesa.
4. **Bancada:** ligar a ponte, motores **suspensos**. Script mínimo do §3 para
   girar cada roda nos dois sentidos e variar a velocidade; conferir sentido
   (usar `invertidoEsquerda/Direita` se uma roda vier ao contrário).
5. **Integrar:** subir o serviço `motores` com `MOTORES_BACKEND=ponteh`, mandar
   comando pelo app (via ponte BLE) e ver a roda responder; testar o watchdog
   (soltar o botão → parar em ~1 s).
6. **Campo:** robô no chão, carregado; medir e ajustar `PASSOS_S_*`→ aqui vira
   faixa de PWM/velocidade, se precisar de mínimo para vencer o atrito.
7. **Testes automatizados** do novo backend (como os do simulado) e atualizar os
   docs (`setup-pi.md`, `COMO-FUNCIONA.md`).

---

## Fontes

- Código atual: [`pi/services/motores/src/motores/acionamento.py`](../pi/services/motores/src/motores/acionamento.py)
  e [`cinematica.py`](../pi/services/motores/src/motores/cinematica.py).
- gpiozero `Motor` / `PhaseEnableMotor` / `Robot`: https://gpiozero.readthedocs.io
