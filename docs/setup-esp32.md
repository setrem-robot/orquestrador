# Setup do Firmware no ESP32

O setup da Ponte BLE via ESP32 é bem simples, sendo necessário apenas seguir o seguinte passo a passo: 

- Fazer a instalação do [Arduino IDE](https://docs.arduino.cc/software/ide/)
- Instalar as placas ESP32 no Arduino IDE [seguindo este tutorial](https://randomnerdtutorials.com/installing-the-esp32-board-in-arduino-ide-windows-instructions/)
- Fazer a instalação da biblioteca `ArduinoJson` no Arduino IDE.

As bibliotecas `BLEDevice`, `BLEServer` e `BLEUtils` **não precisam de instalação separada**: já vêm junto com o pacote de placas ESP32 do Arduino IDE.

Após isso, é só selecionar a placa ESP32 que está sendo utilizda _(No caso dos exemplos está sendo usada a `ESP32-WROOM-DA`)_ e compilar o código em `esp32_ble_bridge/esp32_ble_bridge.ino`.

> [!NOTE]
> Este firmware usa BLE (Bluetooth Low Energy) em vez de Bluetooth Classic (SPP). A troca foi feita para que o app Flutter funcione tanto em Android quanto em iOS — o iOS nunca ofereceu SPP para apps de terceiros. Veja os UUIDs do serviço no topo do `.ino` — eles precisam bater com os mesmos valores em `app/lib/services/robot_connection.dart`.