# Documentação do orquestrador

O [README da raiz](../README.md) conta o que o projeto é e como subir tudo em
quinze minutos. Aqui ficam os detalhes, separados por onde a coisa roda.

## Comece por aqui

- [**Contrato MQTT**](./contrato-mqtt.md) — os tópicos, o formato de cada
  mensagem e o fluxo entre os serviços. **É o documento mais importante do
  repositório:** quase toda mudança que atravessa duas peças passa por ele, e
  mudar um nome de tópico sem mudar aqui é a forma mais fácil de fazer o robô
  aceitar comandos e não se mexer.

## A nuvem

- [**Ambiente local**](./ambiente-local.md) — a nuvem inteira na sua máquina,
  com os mesmos arquivos da produção. É por onde começar: dá para desenvolver e
  testar tudo sem o robô, sem o Pi e sem acesso à VM.
- [**Setup na VM**](./setup-cloud.md) — o broker, o TimescaleDB, o ingestor, a
  API de leitura e o túnel da Cloudflare na VM do LARCC. Inclui as duas causas
  de erro 502 no túnel e como distinguir uma da outra.

## O robô

- [**Setup no Raspberry Pi**](./setup-pi.md) — dependências, os cinco serviços
  em Python (incluindo `telemetria`, a saúde do Pi) e o broker local.
- [**ESP32 — removido**](./setup-esp32.md) — o ESP32 saiu do projeto; a ponte
  Bluetooth foi centralizada no próprio Pi (no [repositório da
  cara](https://github.com/setrem-robot/atlas_ai_v2)).

## Fora deste repositório

- [**site/README.md**](../site/README.md) — publicar a landing page.
- [**atlas_ai_v2**](https://github.com/setrem-robot/atlas_ai_v2) — a face, a voz
  e a conversa com IA. É lá que mora a ponte Bluetooth de hoje.
- [**aplicativo**](https://github.com/setrem-robot/aplicativo) — o app Flutter
  que dirige o robô e mostra a telemetria.
