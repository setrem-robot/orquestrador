# orquestrador — contexto para Claude Code

Firmware do ESP32 + serviços Python do Raspberry Pi + ingestão de telemetria
na nuvem. Para setup e o contrato MQTT completo, leia primeiro
**[`docs/README.md`](./docs/README.md)** (índice para `contrato-mqtt.md`,
`setup-esp32.md`, `setup-pi.md`, `setup-cloud.md`). Este arquivo cobre o que
essa documentação não cobre.

## As três frentes, uma frase cada

- **`esp32/`** — firmware C++/Arduino. Ponte BLE ↔ Serial: valida JSON e
  repassa, não interpreta comandos.
- **`pi/services/`** — cinco serviços Python independentes, cada um seu
  próprio `pyproject.toml`, todos instalados num venv compartilhado
  (`pi/scripts/install.sh`) e rodando como serviço systemd: `serial_ingestor`,
  `orquestrador` (o roteador), `motores`, `gps`, `wifi`. Compartilham a lib
  `robo_common` (tópicos MQTT + `MqttService`).
- **`cloud/`** — Mosquitto remoto + `ingestor` + TimescaleDB + **`api`** +
  **`cloudflared`**, via Docker Compose. Os três primeiros são o caminho de ida
  (robô → banco); os dois últimos são o de volta, que faltava: um celular não
  fala Postgres, e abrir o banco para a internet para que ele falasse seria
  trocar um buraco por um bem maior.
- **`site/`** — a landing page, HTML/CSS/JS puros, publicada pelo Cloudflare
  Pages. Consome as rotas **públicas** da API (sem token, e servindo menos).

## A API é só de leitura, e tem duas portas

`cloud/api/` (FastAPI) nunca escreve — quem grava é o `ingestor`. Ela roda com
um usuário do banco separado, só com `SELECT` (`robo_leitura`): a API é a única
peça exposta à internet, e um bug numa rota não pode ser capaz de apagar meses
de telemetria.

- `/v1/...` exige `Authorization: Bearer`, e serve tudo.
- `/v1/publico/...` não exige nada e serve menos — o resumo, e o trajeto com a
  posição arredondada para ~11 m. A landing page é estática: qualquer token no
  JavaScript dela seria legível por quem abrisse o inspetor, então em vez de
  fingir que é segredo, aquela porta serve menos.

`cloud/api/app/consultas.py` devolve `(sql, parametros)` e **não toca no
banco** — mesmo desenho de `roteador.py` e `motores/cinematica.py`, pelo mesmo
motivo: o que erra por descuido (limite não saturado, campo entrando no SQL sem
validação) fica testável sem infraestrutura. 37 testes:

```bash
cd cloud/api
python3 -m pip install -r requirements-dev.txt   # httpx: o TestClient roda nele
python3 -m unittest discover -s tests
```

Sem o `requirements-dev.txt` a suíte falha na coleta de `test_cabecalhos.py`
com uma mensagem sobre `httpx`, que não é o que está faltando.

**O `intervalo` e o `campo` são interpolados no SQL** — `time_bucket` e o
operador `->>` não aceitam parâmetro para eles. Por isso o primeiro vem de uma
lista fechada (`INTERVALOS`) e o segundo passa por `campo_valido()`. Ao mexer
ali, essa validação é a única coisa entre o cliente e uma injeção.

**Sem dados para testar?** `cloud/scripts/semear-demonstracao.py` enche o banco
com um trajeto plausível em volta do campus, bateria descarregando e comandos
de motor coerentes com a curva. Tudo marcado com `"demo": true`, que é o que
faz `--limpar` nunca tocar em telemetria de verdade.

## BLE, não Bluetooth Classic

O firmware em `esp32/esp32_ble_bridge/` substituiu `esp32/esp32_bt_bridge/`
(removido). Motivo: o app Flutter agora roda em iOS também, e o iOS nunca
ofereceu Bluetooth Classic (SPP) para apps de terceiros. Os UUIDs do serviço
BLE (padrão Nordic UART Service, no topo do `.ino`) **precisam bater** com
`RobotBleIds` em `../app/lib/services/robot_connection.dart`. A validação de
JSON e o formato de mensagem (`{"cmd":"F"}\n`) continuam idênticos — só o
transporte mudou.

## Segurança: movimento é repetido, silêncio é "pare"

Um comando de movimento não vale para sempre. O app repete o mesmo comando a
cada 300 ms enquanto o dedo está no botão; `motores/vigia.py` para os motores se
ficar 1 s sem receber nada. Antes disso, uma conexão que morresse com o dedo no
botão deixava o robô andando sozinho — o `S` do "dedo levantou" nunca chegava.

São três camadas independentes (app repete, ESP32 avisa ao perder o BLE, motores
vigiam o silêncio); o quadro completo está em `docs/contrato-mqtt.md`. Ao mexer
em qualquer ponto desse caminho, pergunte **o que acontece se isto morrer no meio
de um movimento** — e prefira a resposta que para o robô.

`vigia.py` é lógica pura, sem GPIO e sem relógio próprio (quem chama informa o
instante): 12 testes em `pi/services/motores/tests/`, sem hardware e sem esperar
em tempo real.

**E há uma quarta camada, invisível: o cadeado.** `receber()` roda na thread de
rede do paho e `tick()` no laço de `main()`, e os dois mexem na mesma rampa, no
mesmo vigia e no mesmo acionamento. Sem exclusão mútua, um `tick()` que já
estava em curso pode aplicar, **depois** de uma parada de emergência, a
velocidade que tinha lido antes dela — o robô recebe "pare" e continua andando.
`ServicoMotores` serializa as duas entradas com um `RLock` (reentrante porque
`tick()` chama `parada_de_emergencia()`, que também tranca), e
`tests/test_concorrencia.py` prova que ninguém entra na região crítica no meio
de um `aplicar`.

## `roteador.py` é Command Pattern, não dict de funções

`pi/services/orquestrador/src/orquestrador/roteador.py` foi refatorado para
demonstrar POO (disciplina do usuário exige isso): `ComandoRoteavel` é uma
ABC (`abc.ABC` + `@abstractmethod`), e cada tipo de comando do app
("motor", "voz", "parada_emergencia", "wifi") é uma subclasse concreta
registrada em `_ROTAS: dict[str, ComandoRoteavel]`. A função pública
`rotear()` despacha polimorficamente — `_ROTAS[tipo].rotear(comando)` —
sem saber qual subclasse está do outro lado. Testes em
`pi/services/orquestrador/tests/test_roteador.py` (30 casos, `unittest`,
sem broker MQTT nem hardware) comprovam isso, inclusive que
`ComandoRoteavel()` sozinha não instancia (`TypeError`).

Rodar os testes (não precisa instalar nada no Pi real):

```bash
python3 -m venv /tmp/venv-orq && source /tmp/venv-orq/bin/activate
pip install -e pi/services/_common -e pi/services/orquestrador
cd pi/services/orquestrador && python -m unittest discover -s tests -v
```

**O que ainda não foi refatorado:** unificar o `_parar`/`_tratar_sinal`
duplicado nos 5 `main.py`, e dar classes a `wifi/rede.py`. Ficam documentados
aqui como próximo passo, não como pendência esquecida — e o caminho já está
aberto: foi exatamente assim que os motores deixaram de precisar do robô
montado (ver abaixo).

**`wifi/rede.py` já tem teste**, e a linha que dizia o contrário estava errada:
o que precisa de Raspberry Pi é falar com o `nmcli`, não decidir *se* vai falar.
`processar()` recebe um dict do app e devolve outro; trocando `_run_nmcli` por
um dublê, os 16 testes de `pi/services/wifi/tests/` cobrem a validação inteira —
que é a parte que importa, porque esse comando chega por um rádio BLE que não
pede senha a ninguém.

```bash
cd pi/services/wifi && PYTHONPATH="src:../_common/src" python -m unittest discover -s tests
```

## Já orientado a objetos, sem precisar de refactor

`robo_common/mqtt_client.py::MqttService`, `gps/main.py::Posicao` e
`cloud/ingestor/main.py::Ingestor` já são classes com boa encapsulação (estado
privado, API pública enxuta). Não têm herança/polimorfismo — fora
`wifi/rede.py::ErroRede(Exception)` e o que foi adicionado em `roteador.py` e
em `motores/acionamento.py`.

## `motores/` são três camadas, e as duas de baixo rodam sem robô

`pi/services/motores/` foi separado em cinemática, acionamento e serviço:

- **`cinematica.py`** — puro. Comando (as quatro direções, ou `mover` com
  `linear`/`angular`) vira velocidade de cada lado, entre -1 e 1; e a `Rampa`
  acelera até ela sem tranco. Sem GPIO e sem relógio próprio: quem chama informa
  o `dt`, então uma rampa de meio segundo é testada em zero segundos.
- **`acionamento.py`** — `Acionamento` é uma ABC (`abc.ABC` +
  `@abstractmethod`). `AcionamentoStepper` fala com os TMC2209;
  `AcionamentoSimulado` não move nada e anota o que teria feito. É o que
  permite subir o serviço num notebook (`MOTORES_BACKEND=simulado`) e ver no
  log o que o robô faria.
- **`main.py`** — MQTT, vigia e o laço. Não sabe o que é um GPIO.

**O pulso do motor deixou de ser feito em Python.** A versão anterior escrevia
cada flanco do STEP à mão, com `time.sleep(delay)` numa thread — e um sleep de
meio milissegundo não dorme meio milissegundo, dorme o que o escalonador
resolver; a variação ia para o motor como tremor, e mil passos por segundo
custavam duas mil voltas de laço por segundo. Hoje o pino STEP recebe uma onda
quadrada do PWM (gpiozero → lgpio), a frequência **é** a quantidade de passos
por segundo, e mudar a velocidade é escrever um número.

Rodar os 52 testes (sem Pi, sem motor, sem broker):

```bash
cd pi/services/motores && PYTHONPATH="src:../_common/src" python -m unittest discover -s tests
```

## Ambiente desta máquina

Sem hardware real aqui (sem ESP32 conectado, sem Raspberry Pi, sem steppers).
`roteador.py` e as duas camadas de baixo de `motores/` são testáveis porque não
tocam hardware; `gps`, `wifi` e o `serial_ingestor` só são validáveis de verdade
no Pi físico ou com mocks que ninguém escreveu ainda.
