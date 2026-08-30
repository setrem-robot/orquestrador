# Contrato MQTT do Robô (PIE V)

Este documento é a fonte da verdade dos tópicos e formatos de mensagem que os
serviços trocam pelo broker local. O módulo
[`robo_common/topics.py`](../pi/services/_common/src/robo_common/topics.py) é o
reflexo, em código, deste contrato — sempre que mudar um tópico, mude nos dois
lugares.

Toda mensagem é **JSON em uma linha**. A raiz de todos os tópicos é `robo`.

## Fluxo geral

```
App (celular)
   │  BLE (JSON por linha)
   ▼
ESP32  ──(valida JSON, repassa por serial)──►  Pi
                                                 │
                          serial_ingestor ──► robo/comando/entrada
                                                 │
                                          orquestrador (roteia)
                              ┌──────────────────┼───────────────────┐
                              ▼                  ▼                   ▼
                     robo/motores/comando  robo/voz/falar     (outros grupos)

GPS ──► robo/gps/posicao ──┐
Motores ──► robo/.../status ┤  orquestrador espelha ──► robo/telemetria/<tipo>
Bateria ──► robo/sistema/bateria┘                            │
                                                   bridge Mosquitto (out)
                                                             ▼
                                              Broker remoto ──► ingestor ──► TimescaleDB
```

Regra de ouro: **só o que está sob `robo/telemetria/*` é replicado para a
nuvem e persistido**. Quem decide o que entra nesse prefixo é o orquestrador.

## Comandos (entram do mundo externo)

### `robo/comando/entrada`
Publicado pelo `serial_ingestor` com o JSON cru recebido do app, sem
interpretação. Assinado pelo `orquestrador`.

O orquestrador aceita dois formatos (ambos convivem sem conflito):

**Formato compacto** — usado pelo app atual, letra única no campo `cmd`:

```json
{"cmd": "F"}
{"cmd": "B"}
{"cmd": "L"}
{"cmd": "R"}
{"cmd": "S"}
```

| `cmd` | Ação      |
|-------|-----------|
| `F`   | frente    |
| `B`   | trás      |
| `L`   | esquerda  |
| `R`   | direita   |
| `S`   | parar     |

O orquestrador normaliza internamente para o formato expandido com velocidade padrão (60).

**Formato expandido** — retrocompatível, campo `tipo` decide o roteamento:

```json
{"tipo": "motor", "acao": "frente", "velocidade": 80}
{"tipo": "motor", "acao": "mover", "linear": 0.8, "angular": -0.3}
{"tipo": "voz", "texto": "olá, tudo bem?"}
{"tipo": "parada_emergencia"}
{"tipo": "wifi", "acao": "conectar", "ssid": "MinhaRede", "senha": "segredo"}
{"tipo": "rota", "acao": "inicio", "total": 4, "nome": "volta-quadra"}
{"tipo": "rota", "acao": "ponto", "i": 0, "lat": -28.2612, "lon": -54.0234}
{"tipo": "rota", "acao": "fim"}
```

- `motor.acao` ∈ `{frente, tras, esquerda, direita, parar, mover}`;
  `velocidade` ∈ `[0, 100]` (ausente → 60; em `parar` → 0).
- `motor.acao = "mover"` é a forma **contínua**: `linear` e `angular`, cada um
  em `[-1, 1]` (`linear` positivo vai à frente, `angular` positivo gira à
  direita). É o que um joystick produz, e o que permite curvar andando em vez
  de escolher entre andar e girar. Eixo ausente ou malformado vira `0.0`, o que
  faz um `mover` pela metade valer como parada. As quatro direções continuam
  existindo e são atalhos para o mesmo cálculo — ver
  `pi/services/motores/src/motores/cinematica.py`.
- `wifi.acao` ∈ `{conectar, listar, status}` (default `conectar`).
- `rota.acao` ∈ `{inicio, ponto, fim}`. A **rota segura** é planejada no app e
  entregue **fatiada**, porque uma linha BLE é limitada a 512 bytes pelo
  firmware do ESP32 e uma rota com muitos pontos não caberia numa mensagem só. O
  app manda `inicio` (com `total`, e `nome` opcional), um `ponto` por waypoint
  (`i` = índice ≥ 0, `lat`/`lon` na faixa geográfica válida) e `fim`. O
  orquestrador valida e republica cada mensagem em `robo/rota/comando` — ele é
  **sem estado**: quem remonta a rota inteira é o consumidor. Pontos com
  coordenada fora do planeta ou índice malformado são descartados.
- Comandos desconhecidos ou malformados são descartados (logados, não derrubam
  o serviço).

### Movimento é repetido, e silêncio quer dizer "pare"

Um comando de movimento **não vale para sempre**. Enquanto o dedo está no botão,
o app repete o mesmo comando a cada **300 ms**; se o serviço de motores ficar
**1 s** sem receber nada, ele para os motores por conta própria e publica em
`robo/motores/status` com `"motivo": "sem_comando"`.

Isso existe porque o comando de parar viaja pelo mesmo caminho que pode quebrar.
O app manda `F` quando o dedo desce e `S` quando sobe; se a conexão morrer entre
os dois — celular fora de alcance, sem bateria, app fechado, ESP32 travado, cabo
serial solto —, o `S` nunca chega e o robô fica andando sozinho. Repetindo o
comando, a ausência dele passa a ser um sinal em si.

São três camadas independentes, e cada uma cobre o que a anterior não alcança:

| Camada | Cobre | Onde |
|---|---|---|
| App repete o comando | qualquer falha no caminho, inclusive as de baixo | `robot_connection.dart::send` |
| ESP32 manda `parada_emergencia` ao perder o BLE | celular sumiu; é a mais rápida | `esp32_ble_bridge.ino::onDisconnect` |
| Motores param sem comando por 1 s | ESP32 travado, serial solta, Pi sem receber | `motores/vigia.py` |

`COMANDO_TIMEOUT_S=0` desliga a terceira camada. **Só faça isso com um app que
não repete o comando** — do contrário o robô para no meio de todo movimento. Um
app antigo com um Pi atualizado tem exatamente esse sintoma, e o log do serviço
`motores` diz isso com todas as letras.

> **Tudo entra por um único caminho.** O app só tem um canal Bluetooth: o
> ESP32. Não há Bluetooth no Pi. Logo, até a credencial de Wi-Fi viaja como um
> comando comum (app → ESP32 → serial → `serial_ingestor`).

## Domínio (saída do orquestrador)

### `robo/motores/comando`
Comando normalizado para o grupo de Movimento.
```json
{"acao": "frente", "velocidade": 80}
{"acao": "mover", "linear": 0.8, "angular": -0.3}
```

### `robo/voz/falar`
Texto que o grupo de IA deve sintetizar.
```json
{"texto": "olá, tudo bem?"}
```

### `robo/wifi/comando`
Comando de Wi-Fi repassado ao serviço `wifi`, que valida e aplica via `nmcli`.
```json
{"acao": "conectar", "ssid": "MinhaRede", "senha": "segredo"}
```

### `robo/rota/comando`
Cada mensagem da rota segura, já validada, republicada pelo orquestrador.
```json
{"acao": "inicio", "total": 4, "nome": "volta-quadra"}
{"acao": "ponto", "i": 0, "lat": -28.2612, "lon": -54.0234}
{"acao": "fim"}
```
> **Sem consumidor hoje.** Nenhum serviço assina este tópico ainda — o robô
> segue teleoperado, e a rota é um guia planejado no app. Este tópico é o gancho
> para um futuro serviço de navegação que *siga* a rota (malha fechada de GPS),
> que precisa de um Raspberry Pi real para ser desenvolvido e ajustado. Enquanto
> não existir, `robo/rota/*` também não é espelhado para a nuvem.

## Status e telemetria (produzidos pelos serviços)

| Tópico                     | Produtor          | Retained | Exemplo de payload |
|----------------------------|-------------------|----------|--------------------|
| `robo/gps/posicao`         | serviço `gps`     | sim      | `{"lat":-28.2,"lon":-54.0,"fix":true,"satelites":7,"velocidade_kmh":1.2,"ts":...}` |
| `robo/motores/status`      | grupo Movimento   | sim      | `{"acao":"frente","velocidade":80,"esquerda":0.8,"direita":0.8}` |
| `robo/sistema/bateria`     | (a definir)       | sim      | `{"percentual":83,"tensao_v":12.4}` |
| `robo/sistema/wifi`        | serviço `wifi`    | sim      | `{"conectado":true,"ssid":"MinhaRede","ip":"192.168.0.42","ts":...}` |
| `robo/sistema/heartbeat/<servico>` | cada serviço | sim | `{"servico":"gps","status":"online","ts":...}` |
| `robo/sistema/bridge_status` | bridge Mosquitto | sim     | `1` (conectado) / `0` (desconectado) |

## Telemetria espelhada para a nuvem

O orquestrador republica os tópicos vivos selecionados sob `robo/telemetria/<tipo>`:

| Origem                 | Destino (replicado p/ nuvem)  |
|------------------------|-------------------------------|
| `robo/gps/posicao`     | `robo/telemetria/gps`         |
| `robo/motores/status`  | `robo/telemetria/motores`     |
| `robo/sistema/bateria` | `robo/telemetria/bateria`     |
| `robo/sistema/wifi`    | `robo/telemetria/wifi`        |

A bridge do Mosquitto replica `robo/telemetria/#` (direção `out`) para o broker
remoto. O `ingestor` cloud grava cada mensagem na hypertable `telemetria` do
TimescaleDB, usando o campo `ts` do payload como instante do dado.

## Provisionamento de Wi-Fi

O serviço `wifi` assina `robo/wifi/comando` (para onde o orquestrador roteia os
comandos `{"tipo":"wifi"}` vindos do app) e aplica via `nmcli`. Após aplicar,
publica o estado em `robo/sistema/wifi` (retained), que o orquestrador espelha
para a nuvem:

```json
{"conectado": true, "ssid": "MinhaRede", "ip": "192.168.0.42", "ts": 1700000000}
```

> O app recebe do ESP32 o `ack` de que o comando foi aceito. O resultado
> detalhado da conexão (sucesso/IP) hoje só vai para o MQTT — não volta ao
> celular, porque o firmware do ESP32 não tem canal serial→Bluetooth de
> retorno. Se isso for desejável no futuro, basta o ESP32 repassar ao app o que
> chegar na serial.
