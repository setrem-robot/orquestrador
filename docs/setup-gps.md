# Setup do GPS no Raspberry Pi 5

Guia para ligar o módulo **GY-GPS6MV2 (u-blox NEO-6M)** direto no Raspberry Pi 5
pela **UART** e pôr o serviço `gps` publicando a posição no barramento MQTT.

O lado do software já está pronto: `pi/services/gps/` lê NMEA da serial, extrai
a posição e publica em `robo/gps/posicao`. O que este documento cobre é a parte
física e a configuração — que é o que faltava fechar.

- **Módulo:** GY-GPS6MV2 (chip u-blox NEO-6M), NMEA 0183, **9600 baud** por padrão.
- **Ligação:** UART nos pinos GPIO do Pi (`/dev/serial0`).
- **Serviço:** `pi/services/gps` (entrypoint `gps`, unidade `robo-gps.service`).

---

## 1. Ligação física (4 fios)

> [!IMPORTANT]
> **Desligue o Raspberry Pi antes de ligar os fios.** A GY-GPS6MV2 tem regulador
> de 3,3 V embutido e o TX dela é **3,3 V** — seguro para o RX do Pi, **sem
> level shifter**. Isso vale para esta placa; um módulo diferente pode ter
> lógica 5 V, o que **danifica** o RX do Pi. Se trocar de módulo, confira antes.

TX e RX vão **cruzados** (o que um transmite, o outro recebe):

| Pino no GPS | Pino no Pi (físico) | Função no Pi |
|---|---|---|
| `VCC` | **4** (5V) | Alimentação (o módulo regula para 3,3 V; 5 V dá melhor desempenho de antena) |
| `GND` | **6** (GND) | Terra |
| `TX`  | **10** (GPIO15) | **RXD** — o Pi recebe as sentenças NMEA |
| `RX`  | **8** (GPIO14)  | **TXD** — o Pi envia comandos ao módulo (opcional para só ler) |

```
GY-GPS6MV2            Raspberry Pi 5 (header 40 pinos)
   VCC  ───────────────►  pino 4  (5V)
   GND  ───────────────►  pino 6  (GND)
   TX   ───────────────►  pino 10 (GPIO15 / RXD)
   RX   ◄───────────────  pino 8  (GPIO14 / TXD)
```

O `RX` do GPS só é usado para *mandar* comando ao módulo (trocar baud, desligar
sentenças). Para apenas ler a posição, ele pode ficar desconectado — mas ligue
os quatro para não faltar depois.

A **antena** cerâmica precisa ver o céu. Dentro de prédio o fix quase nunca vem;
o primeiro teste tem que ser perto de uma janela ou ao ar livre.

---

## 2. Habilitar a UART do Pi (uma vez)

Por padrão o Raspberry Pi OS usa a UART dos pinos GPIO como **console de login**.
Isso ocupa a mesma porta que o GPS precisa, então há dois passos: **desligar o
console serial** e **ligar o hardware da UART**.

```bash
sudo raspi-config
#   3  Interface Options
#   I6 Serial Port
#     "Would you like a login shell to be accessible over serial?"  -> NÃO
#     "Would you like the serial port hardware to be enabled?"      -> SIM
sudo reboot
```

Isso escreve `enable_uart=1` em `/boot/firmware/config.txt` e remove o
`console=serial0,115200` de `/boot/firmware/cmdline.txt`. Depois do reboot,
confira que a porta existe e para onde aponta:

```bash
ls -l /dev/serial0        # no Pi 5, aponta para ttyAMA0 (a UART dos pinos GPIO)
```

> No Pi 5, `/dev/serial0` é a UART dos pinos 8/10. O Bluetooth fica numa UART
> própria, então habilitar esta não mexe no BLE do robô.

O usuário do serviço precisa estar no grupo `dialout` para abrir a serial (o
mesmo grupo já pedido no [`setup-pi.md`](./setup-pi.md)):

```bash
sudo usermod -aG dialout $USER   # e reabra a sessão
```

---

## 3. Ver as sentenças NMEA cruas (confirmar que o módulo fala)

Antes de subir o serviço, confirme que o Pi está mesmo recebendo dados. Ajuste a
porta para 9600 baud e leia:

```bash
stty -F /dev/serial0 9600
cat /dev/serial0
```

Você deve ver linhas assim rolando (uma vez por segundo):

```
$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47
$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A
```

- **Só vem lixo/caracteres estranhos:** baud errado. O NEO-6M é 9600; confirme
  o `stty`.
- **Não vem nada:** confira a fiação (TX do GPS no pino 10) e se a UART foi
  habilitada (passo 2).
- **Vem `$GP...` mas com vírgulas vazias e `V`:** o módulo fala, mas **ainda não
  tem fix**. Normal no início.

### O primeiro fix

Com o céu à vista, o NEO-6M leva de ~30 s (partida a frio) a alguns minutos para
o primeiro fix. O **LED** da placa ajuda a ler o estado:

- **apagado/aceso fixo:** procurando satélites, sem fix ainda;
- **piscando (~1 Hz):** fix obtido.

Só há posição válida quando a GGA traz `gps_qual > 0` (ou a RMC traz status `A`).
O serviço **só publica com fix** — sem céu, ele fica quieto de propósito.

---

## 4. Configurar e rodar o serviço

O serviço já tem defaults que batem com este módulo, então normalmente **não
precisa configurar nada**. As variáveis (todas opcionais):

| Variável          | Default        | O que é |
|-------------------|----------------|---------|
| `GPS_PORT`        | `/dev/serial0` | porta serial do módulo |
| `GPS_BAUD`        | `9600`         | baud (padrão do NEO-6M) |
| `GPS_INTERVALO_S` | `1`            | intervalo mínimo entre publicações |
| `MQTT_HOST`       | `127.0.0.1`    | broker local |
| `MQTT_PORT`       | `1883`         | porta do broker |

Rodar à mão para testar (com o broker local no ar — ver [`setup-pi.md`](./setup-pi.md)):

```bash
pi/.venv/bin/gps
```

No log você deve ver `Serial do GPS aberta em /dev/serial0 @ 9600 baud.` e, ao
obter fix, linhas `Posição: lat=… lon=… sats=… v=… km/h`.

Como serviço systemd (sobe no boot):

```bash
sudo cp pi/systemd/robo-gps.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now robo-gps
journalctl -u robo-gps -f
```

Ou, mais simples, rode o [`pi/scripts/install.sh`](../pi/scripts/install.sh),
que instala e habilita todos os serviços de uma vez.

---

## 5. Confirmar que a posição chega ao barramento

Com o serviço rodando e um fix válido, veja o tópico de domínio:

```bash
mosquitto_sub -h 127.0.0.1 -t 'robo/gps/posicao' -v
```

Payload esperado (retained, QoS 1):

```json
{"lat":-28.28,"lon":-54.01,"fix":true,"satelites":7,"altitude_m":300.0,"velocidade_kmh":1.2,"rumo":90.0,"ts":1788969600}
```

Daí para a frente o caminho já existe: o `orquestrador` espelha `robo/gps/posicao`
para `robo/telemetria/gps`, o Mosquitto replica para a nuvem, o `ingestor` grava
no TimescaleDB e o app/painel desenham o trajeto. **Nenhum código novo é preciso**
— só o fix chegando aqui.

---

## 6. Validação em campo (o que precisa ser testado de verdade)

A existência do serviço não prova sinal no campus. Antes de dar por pronto:

- [ ] **Sentenças reais** aparecem em `/dev/serial0` (passo 3).
- [ ] **Fix obtido** ao ar livre; `satelites` ≥ 4 e coordenada plausível.
- [ ] A posição no `mosquitto_sub` **bate** com o local real (confira num mapa).
- [ ] A posição chega ao **banco** e às **telas** (painel/app).
- [ ] **Perda de fix:** cobrir a antena / entrar em prédio — o serviço para de
      publicar (não repete a última posição como se fosse atual).
- [ ] **Reconexão:** desconectar e reconectar o GPS — o serviço reabre a serial
      sozinho e volta a publicar.

> [!WARNING]
> **Última posição conhecida ≠ posição atual.** O payload leva `fix` e `ts`
> (carimbo de tempo). Quem consome (app, painel) deve tratar um fix **velho**
> como "sem posição atual", não como onde o robô está agora. Testar a perda de
> fix é o que garante que isso está certo ponta a ponta.

---

## 7. Problemas comuns

| Sintoma | Causa provável | O que fazer |
|---|---|---|
| `Não consegui abrir /dev/serial0` | UART não habilitada, ou fora do grupo `dialout` | Passo 2; reabrir a sessão |
| Serial abre, mas nada chega | TX/RX trocados ou soltos; console serial ainda ativo | Conferir fiação (TX do GPS → pino 10); refazer o passo 2 |
| Só caracteres ilegíveis | baud errado | `GPS_BAUD=9600` (default); `stty -F /dev/serial0 9600` |
| `$GP...` vem, mas nunca há fix | sem céu à vista, ou antena ruim | Testar ao ar livre; esperar a partida a frio |
| Publica posição parada/errada | fix fraco (poucos satélites) | Esperar mais satélites; céu mais aberto |

---

## Fontes

- Código: [`pi/services/gps/src/gps/main.py`](../pi/services/gps/src/gps/main.py)
- Contrato do tópico: [`docs/contrato-mqtt.md`](./contrato-mqtt.md) e
  [`pi/services/_common/src/roboCommon/topics.py`](../pi/services/_common/src/roboCommon/topics.py)
- Instalação dos serviços do Pi: [`docs/setup-pi.md`](./setup-pi.md)
