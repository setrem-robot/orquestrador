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
from robo_common.mqttClient import MqttService

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

parar = False


def tratarSinal(signum, frame) -> None:
    global parar
    logger.info("Sinal %s recebido; encerrando com elegância...", signum)
    parar = True


def lerArquivo(caminho: str) -> str:
    """Lê um arquivo de texto; devolve "" se não der (o parser trata o vazio)."""
    try:
        with open(caminho, encoding="ascii", errors="ignore") as f:
            return f.read()
    except OSError:
        return ""


def vcgencmd(*args: str) -> str:
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


def usoDisco() -> dict | None:
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
    return coletor.usoDisco(st.f_frsize * st.f_blocks, st.f_frsize * st.f_bavail)


#: Onde o kernel expõe a frequência e o governor do primeiro núcleo. O robô lê
#: só o cpu0: num Pi todos os núcleos andam juntos, e ler os quatro encheria o
#: payload sem dizer nada a mais.
_FREQ = "/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq"
_GOVERNOR = "/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor"


def lerCpu(nucleosAnterior: dict[str, tuple[int, int]]) -> tuple[dict, dict]:
    """Monta o bloco `cpu` do payload. Devolve (bloco, leitura atual por núcleo).

    A CPU é a única medida que precisa de duas leituras (é um delta): guardamos
    a leitura por núcleo de uma volta para a próxima. Na primeira volta não há
    delta, então `uso_pct` e `por_nucleo` ficam None/[] e aparecem da segunda
    publicação em diante.
    """
    agora = coletor.nucleosDoProcStat(lerArquivo("/proc/stat"))
    usoPct = None
    porNucleo: list[float | None] = []
    if "cpu" in nucleosAnterior and "cpu" in agora:
        usoPct = coletor.cpuUsoPct(nucleosAnterior["cpu"], agora["cpu"])
    # Núcleos em ordem (cpu0, cpu1, ...), só os que existiam nas duas leituras.
    for nome in sorted(n for n in agora if n != "cpu"):
        if nome in nucleosAnterior:
            porNucleo.append(coletor.cpuUsoPct(nucleosAnterior[nome], agora[nome]))
    bloco = {
        "uso_pct": usoPct,
        "por_nucleo": porNucleo,
        "freq_mhz": coletor.parseFreqKhz(lerArquivo(_FREQ)),
        "governor": lerArquivo(_GOVERNOR).strip() or None,
        "voltagem_v": coletor.parseVolts(vcgencmd("measure_volts")),
    }
    return bloco, agora


def coletar(
    nucleosAnterior: dict[str, tuple[int, int]],
) -> tuple[dict, dict[str, tuple[int, int]]]:
    """Monta o payload da saúde do Pi. Devolve (payload, leitura de CPU atual).

    A maioria das medidas é instantânea; só a CPU é um delta entre duas voltas,
    e por isso o estado dela viaja de uma chamada para a outra.
    """
    loadavg = lerArquivo("/proc/loadavg")
    cpu, nucleosAgora = lerCpu(nucleosAnterior)

    payload = {
        "ts": time.time(),
        "temperatura_c": coletor.parseTemperatura(
            lerArquivo("/sys/class/thermal/thermal_zone0/temp")
        ),
        "throttled": coletor.parseThrottled(vcgencmd("get_throttled")),
        "cpu": cpu,
        "carga": coletor.parseLoadavg(loadavg),
        "processos": coletor.parseProcessos(loadavg),
        "memoria": coletor.parseMeminfo(lerArquivo("/proc/meminfo")),
        "disco": usoDisco(),
        "uptime_s": coletor.parseUptime(lerArquivo("/proc/uptime")),
        "rede": coletor.parseRede(lerArquivo("/proc/net/dev")),
        "wifi": coletor.parseWireless(lerArquivo("/proc/net/wireless"), WIFI_IFACE),
    }
    return payload, nucleosAgora


def main() -> None:
    signal.signal(signal.SIGINT, tratarSinal)
    signal.signal(signal.SIGTERM, tratarSinal)

    mqttSvc = MqttService(
        clientId=SERVICO,
        host=MQTT_HOST,
        port=MQTT_PORT,
        heartbeatTopic=topics.heartbeat(SERVICO),
    )
    mqttSvc.start()
    logger.info("Publicando saúde do Pi em %s a cada %.0fs.", TOPICO, INTERVALO_S)

    nucleosAnterior: dict[str, tuple[int, int]] = {}
    proximoHeartbeat = 0.0
    proximaPublicacao = 0.0

    try:
        while not parar:
            agora = time.monotonic()
            if agora >= proximoHeartbeat:
                mqttSvc.publishJson(
                    topics.heartbeat(SERVICO),
                    {"servico": SERVICO, "status": "online", "ts": time.time()},
                    qos=0,
                    retain=True,
                )
                proximoHeartbeat = agora + HEARTBEAT_INTERVALO_S

            if agora >= proximaPublicacao:
                payload, nucleosAnterior = coletar(nucleosAnterior)
                # QoS 1: é o que faz a bridge GUARDAR e reenviar o que não subiu
                # enquanto a nuvem estava fora de alcance. Sem retain: cada
                # medida é um instante do histórico, não um "último estado".
                mqttSvc.publishJson(TOPICO, payload, qos=1, retain=False)
                thr = payload["throttled"]
                logger.info(
                    "saúde: temp=%s°C cpu=%s%% freq=%sMHz mem=%s%% %s",
                    payload["temperatura_c"],
                    payload["cpu"]["uso_pct"],
                    payload["cpu"]["freq_mhz"],
                    payload["memoria"]["uso_pct"] if payload["memoria"] else "?",
                    "OK" if (thr and thr.get("ok")) else "ATENÇÃO (throttled)",
                )
                proximaPublicacao = agora + INTERVALO_S

            # Passo curto: mantém o loop responsivo ao heartbeat e à parada,
            # sem depender do intervalo (que pode ser longo).
            time.sleep(0.5)
    finally:
        mqttSvc.stop()


if __name__ == "__main__":
    main()
