# ESP32 — removido

> [!IMPORTANT]
> **O ESP32 foi retirado do projeto.** Não há mais firmware para compilar nem
> placa para gravar. Tudo foi **centralizado no Raspberry Pi**.

## O que mudou

Antes, o ESP32 era o gateway Bluetooth: o celular falava BLE com ele, que
validava o JSON e repassava pela serial ao Pi (o serviço `serial-ingestor` lia
essa serial e publicava em `robo/comando/entrada`).

Hoje o **próprio Pi** anuncia o serviço BLE (mesmo padrão Nordic UART Service,
mesmos UUIDs, mesmo formato de mensagem). A ponte roda no repositório
[`RobotEye`](https://github.com/setrem-robot/atlas_ai_v2) em
`src/roboteye/ble/` e publica direto em `robo/comando/entrada` — sem serial e
sem microcontrolador no meio.

Com isso saíram do projeto:

- `esp32/` — o firmware C++/Arduino.
- `pi/services/serial_ingestor/` — o serviço que lia a serial do ESP32.
- a unidade `robo-serial-ingestor.service` e a flag `--com-esp32` do
  `pi/scripts/install.sh`.

## Como subir a ponte BLE hoje

No próprio Pi, pelo repositório `RobotEye`:

```bash
./scripts/setup-raspberry-pi.sh --bluetooth-app --service
```

Os UUIDs do serviço continuam sendo o contrato entre os dois lados e precisam
bater: `RobotBleIds` em `app/lib/services/robotConnection.dart` **e**
`src/roboteye/ble/nus.py` no `RobotEye`.
