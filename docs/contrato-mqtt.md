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
   │  Bluetooth SPP (JSON por linha)
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
{"tipo": "voz", "texto": "olá, tudo bem?"}
{"tipo": "parada_emergencia"}
{"tipo": "wifi", "acao": "conectar", "ssid": "MinhaRede", "senha": "segredo"}
```

- `motor.acao` ∈ `{frente, tras, esquerda, direita, parar}`;
  `velocidade` ∈ `[0, 100]` (ausente → 60; em `parar` → 0).
- `wifi.acao` ∈ `{conectar, listar, status}` (default `conectar`).
- Comandos desconhecidos ou malformados são descartados (logados, não derrubam
  o serviço).

> **Tudo entra por um único caminho.** O app só tem um canal Bluetooth: o
> ESP32. Não há Bluetooth no Pi. Logo, até a credencial de Wi-Fi viaja como um
> comando comum (app → ESP32 → serial → `serial_ingestor`).

## Domínio (saída do orquestrador)

### `robo/motores/comando`
Comando normalizado para o grupo de Movimento.
```json
{"acao": "frente", "velocidade": 80}
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

## Status e telemetria (produzidos pelos serviços)

| Tópico                     | Produtor          | Retained | Exemplo de payload |
|----------------------------|-------------------|----------|--------------------|
| `robo/gps/posicao`         | serviço `gps`     | sim      | `{"lat":-28.2,"lon":-54.0,"fix":true,"satelites":7,"velocidade_kmh":1.2,"ts":...}` |
| `robo/motores/status`      | grupo Movimento   | sim      | `{"acao":"frente","velocidade":80}` |
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
