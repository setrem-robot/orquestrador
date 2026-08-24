# orquestrador — contexto para Claude Code

Firmware do ESP32 + serviços Python do Raspberry Pi + ingestão de telemetria
na nuvem. Para setup e o contrato MQTT completo, leia primeiro
**[`docs/README.md`](./docs/README.md)** (índice para `contrato-mqtt.md`,
`setup-esp32.md`, `setup-pi.md`, `setup-cloud.md`). Este arquivo cobre o que
essa documentação não cobre.

## As três frentes, uma frase cada

- **`esp32/`** — firmware C++/Arduino. Ponte BLE ↔ Serial: valida JSON e
  repassa, não interpreta comandos.
- **`pi/services/`** — cinco serviços Python independentes, cada um seu
  próprio `pyproject.toml`, todos instalados num venv compartilhado
  (`pi/scripts/install.sh`) e rodando como serviço systemd: `serial_ingestor`,
  `orquestrador` (o roteador), `motores`, `gps`, `wifi`. Compartilham a lib
  `robo_common` (tópicos MQTT + `MqttService`).
- **`cloud/`** — Mosquitto remoto + `ingestor` (Python) + TimescaleDB via
  Docker Compose. Só recebe o que está sob `robo/telemetria/#`.

## BLE, não Bluetooth Classic

O firmware em `esp32/esp32_ble_bridge/` substituiu `esp32/esp32_bt_bridge/`
(removido). Motivo: o app Flutter agora roda em iOS também, e o iOS nunca
ofereceu Bluetooth Classic (SPP) para apps de terceiros. Os UUIDs do serviço
BLE (padrão Nordic UART Service, no topo do `.ino`) **precisam bater** com
`RobotBleIds` em `../app/lib/services/robot_connection.dart`. A validação de
JSON e o formato de mensagem (`{"cmd":"F"}\n`) continuam idênticos — só o
transporte mudou.

## Segurança: movimento é repetido, silêncio é "pare"

Um comando de movimento não vale para sempre. O app repete o mesmo comando a
cada 300 ms enquanto o dedo está no botão; `motores/vigia.py` para os motores se
ficar 1 s sem receber nada. Antes disso, uma conexão que morresse com o dedo no
botão deixava o robô andando sozinho — o `S` do "dedo levantou" nunca chegava.

São três camadas independentes (app repete, ESP32 avisa ao perder o BLE, motores
vigiam o silêncio); o quadro completo está em `docs/contrato-mqtt.md`. Ao mexer
em qualquer ponto desse caminho, pergunte **o que acontece se isto morrer no meio
de um movimento** — e prefira a resposta que para o robô.

`vigia.py` é lógica pura, sem GPIO e sem relógio próprio (quem chama informa o
instante): 12 testes em `pi/services/motores/tests/`, sem hardware e sem esperar
em tempo real.

## `roteador.py` é Command Pattern, não dict de funções

`pi/services/orquestrador/src/orquestrador/roteador.py` foi refatorado para
demonstrar POO (disciplina do usuário exige isso): `ComandoRoteavel` é uma
ABC (`abc.ABC` + `@abstractmethod`), e cada tipo de comando do app
("motor", "voz", "parada_emergencia", "wifi") é uma subclasse concreta
registrada em `_ROTAS: dict[str, ComandoRoteavel]`. A função pública
`rotear()` despacha polimorficamente — `_ROTAS[tipo].rotear(comando)` —
sem saber qual subclasse está do outro lado. Testes em
`pi/services/orquestrador/tests/test_roteador.py` (19 casos, `unittest`,
sem broker MQTT nem hardware) comprovam isso, inclusive que
`ComandoRoteavel()` sozinha não instancia (`TypeError`).

Rodar os testes (não precisa instalar nada no Pi real):

```bash
python3 -m venv /tmp/venv-orq && source /tmp/venv-orq/bin/activate
pip install -e pi/services/_common -e pi/services/orquestrador
cd pi/services/orquestrador && python -m unittest discover -s tests -v
```

**Por que só `roteador.py` foi refatorado:** é a única peça do projeto que é
lógica pura, sem GPIO/serial/`nmcli` reais por trás. Os outros candidatos
óbvios a POO — unificar o `_parar`/`_tratar_sinal` duplicado nos 5
`main.py`, ou dar classes a `wifi/rede.py` — tocariam serviços que mexem com
hardware físico (steppers, GPS, Wi-Fi do sistema); não foram feitos por não
haver como testar sem um Raspberry Pi real disponível. Ficam documentados
aqui como próximo passo, não como pendência esquecida.

## Já orientado a objetos, sem precisar de refactor

`robo_common/mqtt_client.py::MqttService`, `motores/stepper.py::Stepper` e
`::ControladorMotores`, `gps/main.py::Posicao`, `cloud/ingestor/main.py::Ingestor`
já são classes com boa encapsulação (estado privado, API pública enxuta).
Não têm herança/polimorfismo — só `wifi/rede.py::ErroRede(Exception)` usa
herança hoje, fora do que foi adicionado em `roteador.py`.

## Ambiente desta máquina

Sem hardware real aqui (sem ESP32 conectado, sem Raspberry Pi, sem steppers).
`roteador.py` é testável porque é lógica pura; o resto dos serviços só é
validável de verdade no Pi físico ou com mocks que ninguém escreveu ainda.
