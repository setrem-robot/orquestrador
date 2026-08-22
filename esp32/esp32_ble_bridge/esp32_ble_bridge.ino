/*
 *  Robô PIE V - Ponte BLE -> Serial
 *
 *  Servidor BLE (GATT, padrão Nordic UART Service) que valida cada linha
 *  recebida como JSON e repassa via Serial ao Raspberry Pi; se inválida,
 *  descarta e responde erro pela característica TX.
 *
 *  BLE em vez de Bluetooth Classic (SPP): o app agora roda em iOS também, e
 *  o iOS nunca ofereceu SPP para apps de terceiros. Validação de JSON e
 *  formato de mensagem não mudaram, só o transporte (GATT em vez de RFCOMM).
 */

#include <ArduinoJson.h>
#include <BLE2902.h>
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>

const char* BLE_DEVICE_NAME = "piev";  // nome que aparece no escaneamento
const size_t MAX_LINE = 512;
const char LINE_DELIMITER = '\n';

// UUIDs do serviço Nordic UART Service (NUS). Mudou aqui, muda em
// `robot_connection.dart` também.
#define SERVICE_UUID "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"
#define RX_CHARACTERISTIC_UUID "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"  // celular escreve
#define TX_CHARACTERISTIC_UUID "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"  // ESP32 notifica

// Serial -> USB (padrão). Serial2 -> pinos UART (GPIO17 TX), libera o USB.
#define PI_SERIAL Serial
const uint32_t PI_BAUD = 115200;  // precisa bater com o lado Pi

BLECharacteristic* txCharacteristic = nullptr;
bool deviceConnected = false;

// Acumula uma mensagem ate o '\n': um pacote BLE pode trazer mais de uma
// mensagem colada, ou uma mensagem partida em dois pacotes.
char lineBuf[MAX_LINE];
size_t lineLen = 0;

void processLine(const char* line, size_t len) {
  if (len == 0) return;

  JsonDocument doc;
  DeserializationError err = deserializeJson(doc, line, len);

  if (err) {
    notifyPhone("{\"ack\":\"erro\",\"motivo\":\"json_invalido\"}\n");
    return;
  }

  serializeJson(doc, PI_SERIAL);
  PI_SERIAL.print('\n');
  notifyPhone("{\"ack\":\"ok\"}\n");
}

void notifyPhone(const char* message) {
  if (!deviceConnected || txCharacteristic == nullptr) return;
  txCharacteristic->setValue((uint8_t*)message, strlen(message));
  txCharacteristic->notify();
}

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

class ServerCallbacks : public BLEServerCallbacks {
  void onConnect(BLEServer* server) override {
    deviceConnected = true;
  }

  void onDisconnect(BLEServer* server) override {
    deviceConnected = false;
    lineLen = 0;
    // Sem isso o ESP32 para de anunciar apos a primeira desconexao
    // (peculiaridade conhecida da biblioteca BLE do ESP32).
    server->startAdvertising();
  }
};

class RxCallbacks : public BLECharacteristicCallbacks {
  void onWrite(BLECharacteristic* characteristic) override {
    String value = characteristic->getValue();
    handleIncomingBytes((const uint8_t*)value.c_str(), value.length());
  }
};

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

void loop() {
  // Vazio de proposito: os dados chegam via RxCallbacks::onWrite, na task do
  // BLE. Nada de delay() aqui -- atrasaria os callbacks do radio.
}
