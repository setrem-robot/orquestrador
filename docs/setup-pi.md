# Setup do Ambiente no Raspberry Pi

O Pi roda o **broker MQTT local** (em container) e os **serviços Python** que
leem o GPS, orquestram os comandos e reportam a saúde do próprio Pi.

## 1. Pré-requisitos

- Raspberry Pi OS (64-bit) com Python 3.11+.
- Docker + plugin Compose:
  ```bash
  curl -fsSL https://get.docker.com | sh
  sudo usermod -aG docker $USER   # e reabra a sessão
  ```
- Acesso à serial (GPS): o usuário precisa estar no grupo `dialout`:
  ```bash
  sudo usermod -aG dialout $USER  # e reabra a sessão
  ```

## 2. Broker MQTT local

O broker escuta **apenas em loopback** (`127.0.0.1:1883`); nada de fora do Pi
o alcança. A telemetria sobe para a nuvem pela *bridge*.

```bash
cd pi

# Configure a bridge para a nuvem (o arquivo real não vai pro git):
cp mosquitto/config/conf.d/bridge.conf.example mosquitto/config/conf.d/bridge.conf
# edite bridge.conf: address da VM, remote_username e remote_password

docker compose up -d
docker compose logs -f mosquitto   # acompanhar
```

## 3. Serviços Python

Todos os serviços compartilham um único virtualenv em `pi/.venv` e a lib
`robo-common`. O script de instalação cuida da ordem correta:

```bash
./pi/scripts/install.sh
```

Serviços instalados:

| Serviço           | O que faz                                              |
|-------------------|--------------------------------------------------------|
| `orquestrador`    | Roteia comandos e espelha telemetria para a nuvem.     |
| `motores`         | Executa o movimento (`robo/motores/comando` → drivers TMC2209). |
| `gps`             | Lê NMEA do GPS → `robo/gps/posicao`. Ligação física em [`setup-gps.md`](./setup-gps.md). |
| `wifi`            | Aplica credencial de Wi-Fi (comando MQTT) → `robo/sistema/wifi`. |
| `telemetria`      | Publica a saúde do próprio Pi → `robo/telemetria/sistema`. |

### Rodar manualmente (para testar)

```bash
pi/.venv/bin/orquestrador
MOTORES_BACKEND=simulado pi/.venv/bin/motores   # simulado: sem GPIO, só loga
GPS_PORT=/dev/serial0 pi/.venv/bin/gps
sudo WIFI_IFACE=wlan0 pi/.venv/bin/wifi   # precisa de root (nmcli)
pi/.venv/bin/telemetria   # saúde do Pi; roda em qualquer máquina (degrada fora do Pi)
```

### Rodar como serviço (systemd)

Cada serviço tem um unit em `pi/systemd/`. Ajuste `User` e os caminhos se o
repo não estiver em `/home/setrem/setrem-robot`, depois:

```bash
sudo cp pi/systemd/robo-orquestrador.service     /etc/systemd/system/
sudo cp pi/systemd/robo-motores.service          /etc/systemd/system/
sudo cp pi/systemd/robo-gps.service              /etc/systemd/system/
sudo cp pi/systemd/robo-wifi.service             /etc/systemd/system/
sudo cp pi/systemd/robo-telemetria.service       /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now robo-orquestrador robo-motores robo-gps robo-wifi robo-telemetria
journalctl -u robo-orquestrador -f
```

## 3.1 Provisionamento de Wi-Fi (serviço `wifi`)

A credencial de Wi-Fi chega como qualquer outro comando: o app envia
`{"tipo":"wifi",...}` pela ponte BLE do Pi; o orquestrador roteia para
`robo/wifi/comando` e o serviço `wifi` aplica via `nmcli`. Por mexer na rede, o
serviço roda como
**root**, e precisa do NetworkManager ativo:

```bash
sudo systemctl enable --now NetworkManager
```

Para testar sem o app, basta injetar o comando no barramento (ver a Verificação
abaixo). O protocolo completo está em
[contrato-mqtt.md](./contrato-mqtt.md#provisionamento-de-wi-fi).

## 4. Verificação rápida

Com o broker no ar, observe o barramento e simule um comando:

```bash
# Em um terminal: assina tudo do robô.
docker exec -it mosquitto-local mosquitto_sub -t 'robo/#' -v

# Em outro: injeta um comando como se viesse do app.
docker exec -it mosquitto-local \
  mosquitto_pub -t robo/comando/entrada -m '{"tipo":"motor","acao":"frente","velocidade":80}'

# Wi-Fi pelo mesmo caminho: o orquestrador roteia para robo/wifi/comando e o
# serviço wifi aplica via nmcli, publicando o estado em robo/sistema/wifi.
docker exec -it mosquitto-local \
  mosquitto_pub -t robo/comando/entrada -m '{"tipo":"wifi","acao":"conectar","ssid":"MinhaRede","senha":"segredo"}'
```

Você deve ver o `orquestrador` republicar em `robo/motores/comando`.

## Configuração por variáveis de ambiente

| Variável                | Default        | Serviço            |
|-------------------------|----------------|--------------------|
| `GPS_PORT`              | `/dev/serial0` | gps                |
| `GPS_BAUD`              | `9600`         | gps                |
| `GPS_INTERVALO_S`       | `1`            | gps                |
| `WIFI_IFACE`            | `wlan0`        | wifi               |
| `MQTT_HOST`             | `127.0.0.1`    | todos              |
| `MQTT_PORT`             | `1883`         | todos              |
| `HEARTBEAT_INTERVALO_S` | `10`           | todos              |
| `LOG_LEVEL`             | `INFO`         | todos              |
