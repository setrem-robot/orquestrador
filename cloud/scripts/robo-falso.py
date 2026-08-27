"""Um robô que não existe, publicando no MQTT como se existisse.

A diferença para o `semear-demonstracao.py` é o caminho percorrido. Aquele
grava direto no banco — rápido, e prova só que a API sabe ler. Este publica no
broker e deixa a corrente inteira funcionar:

    robo-falso ──► mosquitto ──► ingestor ──► TimescaleDB ──► API ──► app

Um ponto que aparece no mapa do celular vindo daqui provou todas essas peças de
uma vez. É o teste que vale a pena rodar antes de levar qualquer coisa para a
VM do LARCC — e o único que pega os erros de encanamento: senha do broker
errada, ingestor sem assinar o tópico, `ts` no formato que o banco não aceita.

Uso:

    python3 cloud/scripts/robo-falso.py                 publica a cada segundo
    python3 cloud/scripts/robo-falso.py --rapido        dez vezes mais rápido
    python3 cloud/scripts/robo-falso.py --host 192.168.1.102

Ctrl-C encerra. Requer `paho-mqtt` (`pip install paho-mqtt`).
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

import telemetria_falsa as falsa

#: De quantos em quantos ciclos cada grandeza é publicada. O robô de verdade
#: não publica tudo na mesma cadência: a posição muda a todo instante, e a
#: bateria não muda em um segundo. Manter essa proporção aqui é o que faz o
#: gráfico e o mapa saírem com a mesma densidade que terão em campo.
CADENCIA = {"gps": 1, "motores": 2, "bateria": 12, "wifi": 12}

_parar = False


def _ao_sinal(_signum, _frame) -> None:
    global _parar
    _parar = True


def conectar(host: str, porta: int, usuario: str, senha: str) -> mqtt.Client:
    cliente = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="robo-falso")
    if usuario:
        cliente.username_pw_set(usuario, senha)
    cliente.connect(host, porta, keepalive=30)
    # `loop_start` numa thread própria: sem ela o cliente não processa o
    # handshake nem reconecta, e as publicações sairiam para o vazio sem erro.
    cliente.loop_start()
    return cliente


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default=os.environ.get("MQTT_HOST", "127.0.0.1"))
    parser.add_argument("--porta", type=int, default=int(os.environ.get("MQTT_PORT", "1883")))
    parser.add_argument("--usuario", default=os.environ.get("MQTT_USERNAME", ""))
    parser.add_argument("--senha", default=os.environ.get("MQTT_PASSWORD", ""))
    parser.add_argument(
        "--rapido",
        action="store_true",
        help="publica dez vezes mais rápido, para encher a tela em um minuto",
    )
    parser.add_argument(
        "--ciclos", type=int, default=0, help="para depois de N ciclos (0 = para no Ctrl-C)"
    )
    argumentos = parser.parse_args()

    signal.signal(signal.SIGINT, _ao_sinal)
    signal.signal(signal.SIGTERM, _ao_sinal)

    intervalo = 0.1 if argumentos.rapido else 1.0

    try:
        cliente = conectar(argumentos.host, argumentos.porta, argumentos.usuario, argumentos.senha)
    except OSError as erro:
        # A causa quase sempre é uma destas duas, e dizer qual poupa a busca.
        print(f"não consegui falar com o broker em {argumentos.host}:{argumentos.porta}: {erro}")
        print("  o ambiente está de pé?  ./cloud/scripts/ambiente-local.sh")
        print("  o endereço está certo?  use --host se o broker não é local")
        return 1

    print(f"publicando em {argumentos.host}:{argumentos.porta} — Ctrl-C para parar")
    carga = falsa.Bateria()
    # `total` grande faz o passeio dar voltas lentas; a fórmula usa total/3
    # voltas, e um robô que completa a volta em dez segundos parece um carrossel.
    total = 900
    indice = 0
    publicadas = 0

    while not _parar:
        agora = datetime.now(tz=timezone.utc)

        a_publicar = []
        if indice % CADENCIA["gps"] == 0:
            a_publicar.append(("gps", falsa.gps(indice, total, agora)))
        if indice % CADENCIA["motores"] == 0:
            a_publicar.append(("motores", falsa.motores(indice, agora)))
        if indice % CADENCIA["bateria"] == 0:
            a_publicar.append(("bateria", carga.proxima(agora, CADENCIA["bateria"] * intervalo)))
        if indice % CADENCIA["wifi"] == 0:
            a_publicar.append(("wifi", falsa.wifi(indice, agora)))

        for tipo, payload in a_publicar:
            # QoS 1 e retido, como o robô de verdade publica: o `retain` é o que
            # faz o app achar o último estado assim que se conecta, em vez de
            # esperar a próxima mensagem chegar.
            cliente.publish(falsa.TOPICOS[tipo], json.dumps(payload), qos=1, retain=True)
            publicadas += 1

        if indice % 20 == 0:
            lat, lon = falsa.posicao(indice, total)
            print(
                f"  ciclo {indice:>5} · {publicadas:>5} mensagens · "
                f"{lat:.5f}, {lon:.5f} · bateria {carga.percentual:.1f}%"
            )

        indice += 1
        if argumentos.ciclos and indice >= argumentos.ciclos:
            break
        time.sleep(intervalo)

    print(f"\nencerrando; {publicadas} mensagens publicadas")
    cliente.loop_stop()
    cliente.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(main())
