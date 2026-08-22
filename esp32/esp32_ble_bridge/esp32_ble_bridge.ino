/*
 *  Robô PIE V - Ponte BLE -> Serial
 *
 *  O QUE FAZ:
 *    - O ESP32 vira um servidor BLE (GATT) no padrão Nordic UART Service
 *      (NUS), que o celular acessa sem pareamento previo.
 *    - As mensagens recebidas na característica RX devem ser strings
 *      terminadas em '\n' (a mesma regra de antes, só o transporte mudou).
 *    - Valida se cada mensagem é um JSON bem-formado.
 *    - Se for válido, repassa via Serial.
 *    - Se for inválido, descarta e responde com erro pela característica TX.
 *
 *  POR QUE BLE E NÃO BLUETOOTH CLASSIC (SPP):
 *    - O app Flutter agora roda em Android E iOS. O iOS nunca ofereceu SPP
 *      para apps de terceiros — só BLE. Esta é a razão inteira da troca.
 *    - O ESP32 suporta os dois modos de rádio nativamente; a lógica de
 *      validação de JSON e o formato das mensagens não mudaram, só a camada
 *      de transporte (GATT em vez de RFCOMM).
 */

#include <ArduinoJson.h>
#include <BLE2902.h>
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>

// ----------------------------------------------------------------------------
// Configuração
// ----------------------------------------------------------------------------
const char* BLE_DEVICE_NAME = "piev";  // nome que aparece no escaneamento
const size_t MAX_LINE = 512;           // tamanho máx de uma mensagem
const char LINE_DELIMITER = '\n';      // fronteira de mensagem

// UUIDs do serviço "Nordic UART Service" (NUS) — um padrão de fato para BLE
// serial-like, reconhecido por várias ferramentas de debug (ex: nRF Connect).
// Se mudar aqui, tem que mudar em `robot_connection.dart` também.
#define SERVICE_UUID "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"
#define RX_CHARACTERISTIC_UUID "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"  // celular escreve
#define TX_CHARACTERISTIC_UUID "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"  // ESP32 notifica

// Para qual porta os dados validados vão (o link com o Raspberry Pi).
//   Serial  -> via cabo USB (padrão, mais simples).
//   Serial2 -> via pinos UART (GPIO17 TX), libera o USB para debug.
#define PI_SERIAL Serial
const uint32_t PI_BAUD = 115200;  // precisa bater com o lado Pi

BLECharacteristic* txCharacteristic = nullptr;
bool deviceConnected = false;

// Buffer de acumulação de uma mensagem até chegar o '\n'. Continua existindo
// mesmo com BLE porque um pacote pode conter mais de uma mensagem colada (ou
// uma mensagem partida em dois pacotes), exatamente como no SPP.
char lineBuf[MAX_LINE];
size_t lineLen = 0;

// ----------------------------------------------------------------------------
// Valida e, se válido, repassa o JSON ao Pi.
// ----------------------------------------------------------------------------
void processLine(const char* line, size_t len) {
  if (len == 0) {
    return;  // linha vazia, ignora
  }

  // documento dinâmico, sem precisar dimensionar manualmente.
  JsonDocument doc;
  DeserializationError err = deserializeJson(doc, line, len);

  if (err) {
    // Não é JSON válido -> NÃO manda nada pro Serial; só avisa o celular.
    notifyPhone("{\"ack\":\"erro\",\"motivo\":\"json_invalido\"}\n");
    return;
  }

  // JSON válido -> repassa via Serial como UMA linha compacta (NDJSON).
  serializeJson(doc, PI_SERIAL);
  PI_SERIAL.print('\n');

  // Confirma ao celular que foi aceito.
  notifyPhone("{\"ack\":\"ok\"}\n");
}

// Manda uma notificação pela característica TX, se houver alguém conectado.
void notifyPhone(const char* message) {
  if (!deviceConnected || txCharacteristic == nullptr) {
    return;
  }
  txCharacteristic->setValue((uint8_t*)message, strlen(message));
  txCharacteristic->notify();
}

// Processa os bytes recebidos em um pacote BLE, um de cada vez, pelo mesmo
// buffer de linha usado no firmware Classic — assim uma mensagem partida
// entre dois pacotes continua funcionando.
void handleIncomingBytes(const uint8_t* data, size_t length) {
  for (size_t i = 0; i < length; i++) {
    char c = (char)data[i];

    if (c == LINE_DELIMITER || c == '\r') {
      lineBuf[lineLen] = '\0';
      processLine(lineBuf, lineLen);
      lineLen = 0;
    } else {
      if (lineLen < MAX_LINE - 1) {
        lineBuf[lineLen++] = c;
      } else {
        lineLen = 0;
        notifyPhone("{\"ack\":\"erro\",\"motivo\":\"linha_muito_longa\"}\n");
      }
    }
  }
}

// ----------------------------------------------------------------------------
// Callbacks do BLE
// ----------------------------------------------------------------------------
class ServerCallbacks : public BLEServerCallbacks {
  void onConnect(BLEServer* server) override {
    deviceConnected = true;
  }

  void onDisconnect(BLEServer* server) override {
    deviceConnected = false;
    lineLen = 0;  // descarta qualquer mensagem parcial do celular anterior
    // Sem isso o ESP32 para de anunciar depois da primeira desconexão — é uma
    // peculiaridade conhecida da biblioteca BLE do ESP32.
    server->startAdvertising();
  }
};

class RxCallbacks : public BLECharacteristicCallbacks {
  void onWrite(BLECharacteristic* characteristic) override {
    String value = characteristic->getValue();
    handleIncomingBytes((const uint8_t*)value.c_str(), value.length());
  }
};

// ----------------------------------------------------------------------------
void setup() {
  PI_SERIAL.begin(PI_BAUD);

  BLEDevice::init(BLE_DEVICE_NAME);
  BLEServer* server = BLEDevice::createServer();
  server->setCallbacks(new ServerCallbacks());

  BLEService* service = server->createService(SERVICE_UUID);

  BLECharacteristic* rxCharacteristic = service->createCharacteristic(
      RX_CHARACTERISTIC_UUID,
      BLECharacteristic::PROPERTY_WRITE | BLECharacteristic::PROPERTY_WRITE_NR);
  rxCharacteristic->setCallbacks(new RxCallbacks());

  txCharacteristic = service->createCharacteristic(
      TX_CHARACTERISTIC_UUID, BLECharacteristic::PROPERTY_NOTIFY);
  txCharacteristic->addDescriptor(new BLE2902());

  service->start();

  BLEAdvertising* advertising = BLEDevice::getAdvertising();
  advertising->addServiceUUID(SERVICE_UUID);
  advertising->setScanResponse(true);
  BLEDevice::startAdvertising();
}

// ----------------------------------------------------------------------------
void loop() {
  // Não há nada para fazer aqui: os dados chegam via `RxCallbacks::onWrite`,
  // que roda na task do BLE (não no loop principal). Deixado vazio de
  // propósito — nada de `delay()`, para não atrasar os callbacks do rádio.
}
