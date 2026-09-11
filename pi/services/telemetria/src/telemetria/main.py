"""telemetria — saúde do próprio Raspberry Pi -> MQTT -> nuvem.

Publica, de tempos em tempos, um retrato do estado do Pi: temperatura,
`throttled` (subtensão/limite térmico), CPU (uso total, por núcleo, frequência,
governor e voltagem), memória e swap, processos, disco, uptime, rede e sinal do
Wi-Fi. É a "telemetria do rpi" — o que permite olhar o histórico e saber que o
robô passou calor, que a fonte fraquejou, que um núcleo saturou ou que o cartão
está enchendo, mesmo tempos depois.

**Publica direto em `robo/telemetria/sistema`, e não num tópico de domínio.**
Os outros serviços (gps, motores) publicam no domínio deles e deixam o
`orquestrador` decidir o que espelhar para `robo/telemetria/#` — porque a
posição do GPS também tem consumidores locais. A saúde do Pi não tem: ela só
existe para o histórico. Mandá-la direto para `robo/telemetria/` a leva para a
nuvem pela bridge sem depender de o roteador estar no ar, e é onde ela é útil.

**Store-and-forward é do broker, não daqui.** Este serviço só publica em QoS 1;
guardar o que não subiu enquanto o LARCC está fora de alcance, e reenviar ao
reconectar, é o Mosquitto local com `persistence` + fila grande + bridge
(`cleansession false`). Ver `pi/mosquitto/apt/robo.conf.example`. Por isso o
`ts` vai no payload: o ingestor grava por ele, então o que sobe atrasado entra
no banco na hora em que foi medido, não na hora em que chegou.

Configuração por variáveis de ambiente:
    TELEMETRIA_INTERVALO_S   (default 30; entre publicações)
    TELEMETRIA_WIFI_IFACE    (default wlan0)
    VCGENCMD                 (default vcgencmd; caminho do binário)
    MQTT_HOST                (default 127.0.0.1)
    MQTT_PORT                (default 1883)
    HEARTBEAT_INTERVALO_S    (default 10)
    LOG_LEVEL                (default INFO)
"""

from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
import time

from robo_common import topics
from robo_common.mqtt_client import MqttService

from telemetria import coletor

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("telemetria")

SERVICO = "telemetria"
INTERVALO_S = float(os.environ.get("TELEMETRIA_INTERVALO_S", "30"))
WIFI_IFACE = os.environ.get("TELEMETRIA_WIFI_IFACE", "wlan0")
VCGENCMD = os.environ.get("VCGENCMD", "vcgencmd")
MQTT_HOST = os.environ.get("MQTT_HOST", "127.0.0.1")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
HEARTBEAT_INTERVALO_S = float(os.environ.get("HEARTBEAT_INTERVALO_S", "10"))

TOPICO = topics.telemetria("sistema")

_parar = False


def _tratar_sinal(signum, _frame) -> None:
    global _parar
    logger.info("Sinal %s recebido; encerrando com elegância...", signum)
    _parar = True


def _ler_arquivo(caminho: str) -> str:
    """Lê um arquivo de texto; devolve "" se não der (o parser trata o vazio)."""
    try:
        with open(caminho, encoding="ascii", errors="ignore") as f:
            return f.read()
    except OSError:
        return ""


def _vcgencmd(*args: str) -> str:
    """Roda `vcgencmd <args>`; "" se o binário não existe (não é um Pi) ou falha.

    Numa máquina que não é Raspberry, `vcgencmd` não existe — e isso não é erro,
    é o robô rodando fora do robô. O parser recebe "" e o campo vira None.
    """
    if shutil.which(VCGENCMD) is None:
        return ""
    try:
        return subprocess.run(
            [VCGENCMD, *args], capture_output=True, text=True, timeout=5
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _uso_disco() -> dict | None:
    # `os.statvfs` não existe fora de sistemas POSIX (numa máquina de mesa
    # Windows, por exemplo). `getattr` em vez de chamada direta para o serviço
    # rodar degradado onde não há `/`, como fazem os outros serviços do robô.
    statvfs = getattr(os, "statvfs", None)
    if statvfs is None:
        return None
    try:
        st = statvfs("/")
    except OSError:
        return None
    return coletor.uso_disco(st.f_frsize * st.f_blocks, st.f_frsize * st.f_bavail)


#: Onde o kernel expõe a frequência e o governor do primeiro núcleo. O robô lê
#: só o cpu0: num Pi todos os núcleos andam juntos, e ler os quatro encheria o
#: payload sem dizer nada a mais.
_FREQ = "/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq"
_GOVERNOR = "/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor"


def _cpu(nucleos_anterior: dict[str, tuple[int, int]]) -> tuple[dict, dict]:
    """Monta o bloco `cpu` do payload. Devolve (bloco, leitura atual por núcleo).

    A CPU é a única medida que precisa de duas leituras (é um delta): guardamos
    a leitura por núcleo de uma volta para a próxima. Na primeira volta não há
    delta, então `uso_pct` e `por_nucleo` ficam None/[] e aparecem da segunda
    publicação em diante.
    """
    agora = coletor.nucleos_do_proc_stat(_ler_arquivo("/proc/stat"))
    uso_pct = None
    por_nucleo: list[float | None] = []
    if "cpu" in nucleos_anterior and "cpu" in agora:
        uso_pct = coletor.cpu_uso_pct(nucleos_anterior["cpu"], agora["cpu"])
    # Núcleos em ordem (cpu0, cpu1, ...), só os que existiam nas duas leituras.
    for nome in sorted(n for n in agora if n != "cpu"):
        if nome in nucleos_anterior:
            por_nucleo.append(coletor.cpu_uso_pct(nucleos_anterior[nome], agora[nome]))
    bloco = {
        "uso_pct": uso_pct,
        "por_nucleo": por_nucleo,
        "freq_mhz": coletor.parse_freq_khz(_ler_arquivo(_FREQ)),
        "governor": _ler_arquivo(_GOVERNOR).strip() or None,
        "voltagem_v": coletor.parse_volts(_vcgencmd("measure_volts")),
    }
    return bloco, agora


def coletar(
    nucleos_anterior: dict[str, tuple[int, int]],
) -> tuple[dict, dict[str, tuple[int, int]]]:
    """Monta o payload da saúde do Pi. Devolve (payload, leitura de CPU atual).

    A maioria das medidas é instantânea; só a CPU é um delta entre duas voltas,
    e por isso o estado dela viaja de uma chamada para a outra.
    """
    loadavg = _ler_arquivo("/proc/loadavg")
    cpu, nucleos_agora = _cpu(nucleos_anterior)

    payload = {
        "ts": time.time(),
        "temperatura_c": coletor.parse_temperatura(
            _ler_arquivo("/sys/class/thermal/thermal_zone0/temp")
        ),
        "throttled": coletor.parse_throttled(_vcgencmd("get_throttled")),
        "cpu": cpu,
        "carga": coletor.parse_loadavg(loadavg),
        "processos": coletor.parse_processos(loadavg),
        "memoria": coletor.parse_meminfo(_ler_arquivo("/proc/meminfo")),
        "disco": _uso_disco(),
        "uptime_s": coletor.parse_uptime(_ler_arquivo("/proc/uptime")),
        "rede": coletor.parse_rede(_ler_arquivo("/proc/net/dev")),
        "wifi": coletor.parse_wireless(_ler_arquivo("/proc/net/wireless"), WIFI_IFACE),
    }
    return payload, nucleos_agora


def main() -> None:
    signal.signal(signal.SIGINT, _tratar_sinal)
    signal.signal(signal.SIGTERM, _tratar_sinal)

    mqtt_svc = MqttService(
        client_id=SERVICO,
        host=MQTT_HOST,
        port=MQTT_PORT,
        heartbeat_topic=topics.heartbeat(SERVICO),
    )
    mqtt_svc.start()
    logger.info("Publicando saúde do Pi em %s a cada %.0fs.", TOPICO, INTERVALO_S)

    nucleos_anterior: dict[str, tuple[int, int]] = {}
    proximo_heartbeat = 0.0
    proxima_publicacao = 0.0

    try:
        while not _parar:
            agora = time.monotonic()
            if agora >= proximo_heartbeat:
                mqtt_svc.publish_json(
                    topics.heartbeat(SERVICO),
                    {"servico": SERVICO, "status": "online", "ts": time.time()},
                    qos=0,
                    retain=True,
                )
                proximo_heartbeat = agora + HEARTBEAT_INTERVALO_S

            if agora >= proxima_publicacao:
                payload, nucleos_anterior = coletar(nucleos_anterior)
                # QoS 1: é o que faz a bridge GUARDAR e reenviar o que não subiu
                # enquanto a nuvem estava fora de alcance. Sem retain: cada
                # medida é um instante do histórico, não um "último estado".
                mqtt_svc.publish_json(TOPICO, payload, qos=1, retain=False)
                thr = payload["throttled"]
                logger.info(
                    "saúde: temp=%s°C cpu=%s%% freq=%sMHz mem=%s%% %s",
                    payload["temperatura_c"],
                    payload["cpu"]["uso_pct"],
                    payload["cpu"]["freq_mhz"],
                    payload["memoria"]["uso_pct"] if payload["memoria"] else "?",
                    "OK" if (thr and thr.get("ok")) else "ATENÇÃO (throttled)",
                )
                proxima_publicacao = agora + INTERVALO_S

            # Passo curto: mantém o loop responsivo ao heartbeat e à parada,
            # sem depender do intervalo (que pode ser longo).
            time.sleep(0.5)
    finally:
        mqtt_svc.stop()


if __name__ == "__main__":
    main()
