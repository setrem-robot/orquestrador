# orquestrador — o corpo da Atlas

Este repositório é a parte da Atlas que **se move, se localiza e se comunica**:
os serviços que rodam no Raspberry Pi, o firmware do ESP32 e a nuvem que guarda
e serve a telemetria.

A Atlas é um robô autônomo do curso de Engenharia de Computação da SETREM, e
está espalhada por três repositórios:

| Repositório | O que é | O que faz |
|---|---|---|
| [**orquestrador**](https://github.com/setrem-robot/orquestrador) *(este)* | o corpo | motores, GPS, Wi-Fi, telemetria, nuvem |
| [**atlas_ai_v2**](https://github.com/setrem-robot/atlas_ai_v2) | a cara | face animada, voz e conversa com IA |
| [**aplicativo**](https://github.com/setrem-robot/aplicativo) | o controle | app Flutter: dirigir e ver os dados |

---

## Índice

- [O que funciona hoje](#o-que-funciona-hoje)
- [Como as peças se encaixam](#como-as-peças-se-encaixam)
- [Começando do zero](#começando-do-zero) — 15 minutos até ver dados na tela
- [As três frentes](#as-três-frentes)
- [Rodando os testes](#rodando-os-testes)
- [Documentação completa](#documentação-completa)
- [O que ainda falta](#o-que-ainda-falta)

---

## O que funciona hoje

Nada aqui é planejamento: tudo nesta lista roda e foi verificado.

**O caminho de ida — do robô até o banco.** O robô publica telemetria num broker
MQTT local; esse broker repassa (*bridge*) para o broker da nuvem; um ingestor
lê e grava num TimescaleDB. A ponte guarda até 100 mil mensagens em disco
enquanto a rede estiver fora, o que dá cerca de catorze horas de queda sem
perder um ponto.

**O caminho de volta — do banco até o celular.** Uma API de leitura (FastAPI)
serve o histórico, e o app Flutter mostra em quatro telas: estado agora, trajeto
no mapa, gráficos e os eventos crus. A API está publicada na internet por um
túnel da Cloudflare, com HTTPS válido.

**Os motores.** O comando do app vira velocidade de cada lado por cinemática
diferencial, passa por uma rampa de aceleração e sai como onda quadrada do PWM
nos drivers TMC2209. Um vigia para tudo se ficar um segundo sem receber comando.

**A landing page.** HTML puro, consumindo as rotas públicas da API — as que não
pedem token e mostram menos.

O que **não** é verdade ainda está em [O que ainda falta](#o-que-ainda-falta),
com a mesma honestidade.

---

## Como as peças se encaixam

```
       CELULAR                    RASPBERRY PI                      NUVEM
    ┌───────────┐            ┌──────────────────┐          ┌──────────────────┐
    │ app       │───BLE─────▶│ ponte Bluetooth  │          │                  │
    │ Flutter   │            │ (no repo da cara)│          │                  │
    │           │            │        │         │          │                  │
    │  dirigir  │            │        ▼         │          │                  │
    │           │            │  robo/comando/   │          │                  │
    │           │            │     entrada      │          │                  │
    │           │            │        │         │          │                  │
    │           │            │        ▼         │          │                  │
    │           │            │  ORQUESTRADOR    │          │                  │
    │           │            │   (o roteador)   │          │                  │
    │           │            │    ╱   │   ╲     │          │                  │
    │           │            │   ▼    ▼    ▼    │          │                  │
    │           │            │ motores gps wifi │          │                  │
    │           │            │        │         │          │                  │
    │           │            │        ▼         │          │                  │
    │           │            │  mosquitto ──────┼──bridge──▶ mosquitto        │
    │           │            │    (local)       │   MQTT   │      │           │
    │           │            └──────────────────┘          │      ▼           │
    │           │                                          │  ingestor        │
    │           │                                          │      │           │
    │  ver os   │◀───────── HTTPS ─────────────────────────┤      ▼           │
    │   dados   │        (túnel Cloudflare)                │  TimescaleDB     │
    └───────────┘                                          │      │           │
                                                           │      ▼           │
    landing page ◀───── rotas públicas ────────────────────┤     API          │
                                                           └──────────────────┘
```

Duas ideias explicam quase todo o desenho:

**Ninguém fala direto com ninguém.** Tudo passa pelo MQTT, e cada serviço só
conhece os tópicos de que precisa. Trocar o driver do motor não toca no GPS;
trocar o transporte do celular não toca em nenhum dos dois. Os nomes dos tópicos
vivem num lugar só — `pi/services/_common/src/robo_common/topics.py` — e o
formato de cada mensagem está em [`docs/contrato-mqtt.md`](./docs/contrato-mqtt.md).

**A API nunca escreve.** Quem grava é o ingestor. A API roda com um usuário do
banco que só tem `SELECT`, porque ela é a única peça exposta à internet e um bug
numa rota não pode ser capaz de apagar meses de telemetria.

---

## Começando do zero

Você não precisa do robô, nem do Raspberry Pi, nem de acesso à VM da faculdade.
A nuvem inteira sobe na sua máquina com os **mesmos arquivos** que vão para a
produção, e há um robô falso para gerar dados.

### 1. Docker

```bash
git clone https://github.com/setrem-robot/orquestrador.git
cd orquestrador
sudo ./cloud/scripts/instalar-docker-wsl.sh   # no WSL; num Linux comum, o Docker da sua distro serve
```

### 2. Subir a nuvem

```bash
./cloud/scripts/ambiente-local.sh
```

Na primeira vez ele baixa as imagens e compila a API — alguns minutos. Depois
sobe em segundos. Ele cria o arquivo de configuração sozinho, **já preenchido**:
nenhuma senha precisa ser inventada antes da primeira subida. No fim, confere
cada peça e imprime o endereço e o token para pôr no app.

### 3. Gerar dados

Sem isso as telas ficam vazias — e tela vazia não distingue "está quebrado" de
"não há o que mostrar".

```bash
./cloud/scripts/ambiente-local.sh --robo
```

Isso deixa um robô falso publicando ao vivo, pelo caminho inteiro:
`robô falso → mosquitto → ingestor → TimescaleDB → API`. É o teste que pega os
erros de encanamento que nenhum teste de unidade pega.

### 4. Ver

```bash
curl http://127.0.0.1:8000/saude
cd site && python3 -m http.server 8080     # e abra http://localhost:8080
```

Para ver no celular, falta uma coisa só, e é um comando:

```bash
./cloud/scripts/ambiente-local.sh --rede
```

> **Por que isso é preciso:** no WSL os containers ficam atrás de uma rede que só
> existe dentro dele, e o celular não a alcança. O script descobre em qual modo
> o seu WSL está e faz o certo para cada caso. É a parte que mais dá trabalho, e
> está explicada em detalhe em [`docs/ambiente-local.md`](./docs/ambiente-local.md).

| Comando | O que faz |
|---|---|
| `ambiente-local.sh` | sobe tudo e confere |
| `ambiente-local.sh --robo` | sobe e deixa um robô falso publicando |
| `ambiente-local.sh --logs` | acompanha o que cada peça está fazendo |
| `ambiente-local.sh --rede` | libera o acesso do celular |
| `ambiente-local.sh --endereco` | mostra o que pôr no app |
| `ambiente-local.sh --parar` | desliga (os dados ficam) |
| `ambiente-local.sh --zerar` | desliga e apaga o banco |

---

## As três frentes

Cada pasta é um ambiente de desenvolvimento próprio, com suas ferramentas.

### `pi/` — o que roda no robô

Cinco serviços Python independentes, cada um com seu `pyproject.toml`, todos
instalados num ambiente compartilhado e rodando como serviço do systemd:

| Serviço | Responsabilidade |
|---|---|
| `orquestrador` | o roteador: recebe comando do app e manda para quem executa |
| `motores` | cinemática, rampa de aceleração e os drivers TMC2209 |
| `gps` | lê o módulo e publica posição |
| `wifi` | troca de rede sem cabo nem teclado |
| `serial_ingestor` | recebe do ESP32 pela serial (herdado; hoje o BLE roda no próprio Pi) |

Os motores são três camadas, e **as duas de baixo rodam sem robô**: a cinemática
é matemática pura, e o acionamento tem uma implementação simulada
(`MOTORES_BACKEND=simulado`) que anota no log o que teria feito. Dá para
desenvolver o serviço inteiro num notebook.

→ [`docs/setup-pi.md`](./docs/setup-pi.md)

### `cloud/` — o que roda na VM

Cinco containers no Docker Compose: `mosquitto`, `ingestor`, `timescaledb`,
`api` e `cloudflared`. Os três primeiros são o caminho de ida; os dois últimos
são o de volta, que faltava — um celular não fala Postgres, e abrir o banco para
a internet para que ele falasse seria trocar um buraco por um bem maior.

**A mesma definição vale na sua máquina e na VM.** O que muda são duas coisas:
lá você para de passar o `compose.local.yml`, e troca as senhas. Nada mais.

→ [`docs/setup-cloud.md`](./docs/setup-cloud.md) · [`docs/ambiente-local.md`](./docs/ambiente-local.md)

### `esp32/` — a ponte Bluetooth (herdada)

Firmware C++/Arduino que faz ponte BLE ↔ Serial. **Está sendo aposentado:** com
o Raspberry Pi 5 tendo Bluetooth próprio, a ponte passou a rodar no próprio Pi,
no repositório da cara. O firmware continua aqui porque ainda funciona e serve
de reserva.

→ [`docs/setup-esp32.md`](./docs/setup-esp32.md)

### `site/` — a landing page

HTML, CSS e JavaScript puros, sem framework e sem build. Consome só as rotas
públicas da API. Um único arquivo muda entre instalações: `site/config.js`.

→ [`site/README.md`](./site/README.md)

---

## Rodando os testes

Nenhum deles precisa de robô, de broker ou de banco: tudo que depende de
hardware fica atrás de uma interface, e o que é lógica pura é testado como
lógica pura.

```bash
# tudo de uma vez, sem instalar nada na sua máquina
./scripts/testar.sh
```

Ou cada frente separada, se preferir:

```bash
# serviços do Pi
cd pi/services
python3 -m venv /tmp/venv && . /tmp/venv/bin/activate
pip install -e ./_common -e ./motores -e ./orquestrador
(cd motores && python -m pytest -q)          # 45 testes
(cd orquestrador && python -m pytest -q)     # 22 testes

# API da nuvem
cd cloud/api && python3 -m pytest -q tests/  # 27 testes
```

---

## Documentação completa

| Documento | Para quê |
|---|---|
| [`docs/contrato-mqtt.md`](./docs/contrato-mqtt.md) | os tópicos, o formato das mensagens e o fluxo entre serviços |
| [`docs/ambiente-local.md`](./docs/ambiente-local.md) | a nuvem inteira na sua máquina, e como levar para a VM |
| [`docs/setup-cloud.md`](./docs/setup-cloud.md) | a VM: broker, banco, ingestor, API e o túnel da Cloudflare |
| [`docs/setup-pi.md`](./docs/setup-pi.md) | o Raspberry Pi: dependências, serviços e broker local |
| [`docs/setup-esp32.md`](./docs/setup-esp32.md) | compilar e gravar o firmware da ponte |
| [`site/README.md`](./site/README.md) | publicar a landing page |

---

## O que ainda falta

Nesta ordem, que é a de quanto cada uma trava as outras.

**Um broker só, no Pi.** Hoje há um conflito real: o repositório da cara instala
um Mosquitto pelo `apt`, e o `docker-compose.yml` daqui sobe outro na mesma
porta 1883. O segundo a subir falha com *"Address already in use"*, e o sintoma
não parece de broker: o app conecta, os comandos chegam ao Pi, e o robô não se
mexe — porque a ponte publica com sucesso num broker que ninguém mais escuta.
Ver `pi/mosquitto/apt/robo.conf.example`.

**Os motores no hardware.** A cinemática, a rampa e o vigia têm 45 testes, e o
acionamento simulado prova o serviço inteiro num notebook. O que nunca girou é
um motor de verdade com esse código.

**O GPS.** O serviço existe e publica no tópico certo, mas o módulo ainda não
está montado no robô. É por isso que os dados de trajeto que você vê hoje vêm do
robô falso.

**A nuvem na VM do LARCC.** Está tudo pronto e testado, rodando na máquina de
mesa de um dos integrantes. Migrar é parar de passar um arquivo e trocar as
senhas — o passo a passo está em [`docs/ambiente-local.md`](./docs/ambiente-local.md),
seção 6. Enquanto isso não acontece, a API pública só responde com aquela
máquina ligada.

**A Atlas não fala o que o app manda.** Existe o tópico `robo/voz/falar`, e nada
o escuta: o repositório da cara ainda não reage a MQTT. Do mesmo jeito, ela não
sabe a própria bateria — `robo/telemetria/bateria` também não tem ouvinte.

**Refatorações conhecidas.** O `_parar`/`_tratar_sinal` está duplicado nos cinco
`main.py`, e `wifi/rede.py` ainda é função solta em vez de classe. Ficam
registradas aqui como próximo passo, não como pendência esquecida.

---

<div align="center">

**Projeto Interdisciplinar de Extensão · Engenharia de Computação**
**Sociedade Educacional Três de Maio — Três de Maio, RS**

</div>
