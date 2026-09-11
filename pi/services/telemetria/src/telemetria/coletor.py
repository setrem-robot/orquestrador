"""Leitura da saúde do Pi, separada de onde os números vêm.

Tudo aqui é função pura sobre texto: recebe o conteúdo cru de um arquivo de
`/proc` ou a saída de um `vcgencmd` e devolve o número já tratado. Nada abre
arquivo, roda comando ou fala com o relógio — quem faz isso é o `main.py`.

O motivo é o de sempre neste repositório (ver `cinematica.py`, `roteador.py`,
`consultas.py`): o que erra por descuido — uma conta de porcentagem, um bit de
`throttled` lido ao contrário — fica testável sem um Raspberry Pi na mesa. E
neste caso importa em dobro, porque a máquina de quem escreve o código **não**
tem `vcgencmd` nem os mesmos arquivos de `/proc`.
"""

from __future__ import annotations

#: Bits do `vcgencmd get_throttled`. Os quatro de baixo são "agora"; os quatro
#: de cima (16-19) são "aconteceu desde que ligou" — e estes são os que contam
#: para saber se a fonte é fraca ou se o robô passou calor sem ninguém ver.
_BITS_THROTTLED = {
    0: "subtensao_agora",
    1: "frequencia_limitada_agora",
    2: "throttling_agora",
    3: "limite_termico_agora",
    16: "subtensao_ja_ocorreu",
    17: "frequencia_limitada_ja_ocorreu",
    18: "throttling_ja_ocorreu",
    19: "limite_termico_ja_ocorreu",
}


def parse_temperatura(raw: str) -> float | None:
    """`/sys/class/thermal/thermal_zone0/temp` (milésimos de grau) -> °C.

    "54321\\n" -> 54.3. Vazio ou lixo -> None, para o payload dizer "não sei"
    em vez de mentir um zero.
    """
    raw = raw.strip()
    if not raw:
        return None
    try:
        return round(int(raw) / 1000.0, 1)
    except ValueError:
        return None


def parse_throttled(raw: str) -> dict | None:
    """`vcgencmd get_throttled` -> flags + o hex cru.

    A saída é "throttled=0x50005". Guardamos o hex (para conferência) e
    abrimos cada bit num booleano nomeado — quem lê o histórico não deve
    precisar decorar a tabela de bits da Raspberry.
    """
    raw = raw.strip()
    if "=" in raw:
        raw = raw.split("=", 1)[1]
    raw = raw.strip()
    if not raw:
        return None
    try:
        valor = int(raw, 16) if raw.lower().startswith("0x") else int(raw)
    except ValueError:
        return None
    flags = {nome: bool(valor & (1 << bit)) for bit, nome in _BITS_THROTTLED.items()}
    flags["hex"] = hex(valor)
    # Resumo: um só booleano para "tem algo errado com energia/temperatura",
    # que é o que um alerta olha primeiro.
    flags["ok"] = valor == 0
    return flags


def parse_loadavg(raw: str) -> dict | None:
    """`/proc/loadavg` -> carga média de 1, 5 e 15 minutos.

    "0.52 0.41 0.38 1/234 5678" -> {"1m":0.52,"5m":0.41,"15m":0.38}.
    """
    partes = raw.split()
    if len(partes) < 3:
        return None
    try:
        return {"1m": float(partes[0]), "5m": float(partes[1]), "15m": float(partes[2])}
    except ValueError:
        return None


def parse_meminfo(raw: str) -> dict | None:
    """`/proc/meminfo` -> memória em MB e porcentagem em uso.

    Usa `MemAvailable` (o que dá para usar de fato, contando cache liberável),
    não `MemFree` — `MemFree` num Linux saudável é sempre baixo e assusta à toa.
    """
    campos: dict[str, int] = {}
    for linha in raw.splitlines():
        if ":" not in linha:
            continue
        chave, _, resto = linha.partition(":")
        valor = resto.strip().split()
        if valor and valor[0].isdigit():
            campos[chave.strip()] = int(valor[0])  # em kB
    total = campos.get("MemTotal")
    disponivel = campos.get("MemAvailable")
    if not total or disponivel is None:
        return None
    usado = total - disponivel
    return {
        "total_mb": round(total / 1024),
        "disponivel_mb": round(disponivel / 1024),
        "uso_pct": round(usado / total * 100, 1),
    }


def parse_uptime(raw: str) -> float | None:
    """`/proc/uptime` -> segundos ligado (primeiro campo)."""
    partes = raw.split()
    if not partes:
        return None
    try:
        return round(float(partes[0]), 1)
    except ValueError:
        return None


def uso_disco(total_bytes: int, livre_bytes: int) -> dict | None:
    """Uso do disco a partir do total e do livre (de `os.statvfs`).

    Recebe números, não um caminho, para ser testável — quem chama o
    `statvfs` de verdade é o `main.py`.
    """
    if total_bytes <= 0:
        return None
    usado = total_bytes - livre_bytes
    return {
        "total_gb": round(total_bytes / 1024**3, 1),
        "livre_gb": round(livre_bytes / 1024**3, 1),
        "uso_pct": round(usado / total_bytes * 100, 1),
    }


def cpu_uso_pct(anterior: tuple[int, int], atual: tuple[int, int]) -> float | None:
    """Uso de CPU entre duas leituras da PRIMEIRA linha de `/proc/stat`.

    O `/proc/stat` dá totais acumulados desde o boot; uso instantâneo é a
    diferença entre duas leituras. Cada leitura é `(ocupado, total)`, montada
    por `total_e_ocupado_do_proc_stat`. Sem delta (duas leituras iguais)
    devolve None — não há intervalo para medir.
    """
    ocup0, tot0 = anterior
    ocup1, tot1 = atual
    d_total = tot1 - tot0
    if d_total <= 0:
        return None
    d_ocupado = ocup1 - ocup0
    return round(max(0.0, min(100.0, d_ocupado / d_total * 100)), 1)


def total_e_ocupado_do_proc_stat(raw: str) -> tuple[int, int] | None:
    """Primeira linha de `/proc/stat` -> (ocupado, total) em jiffies.

    "cpu  123 4 56 789 10 0 2 0 0 0" — os campos são user, nice, system, idle,
    iowait, irq, softirq, steal... O "ocupado" é tudo menos idle+iowait.
    """
    primeira = raw.splitlines()[0] if raw.strip() else ""
    partes = primeira.split()
    if len(partes) < 5 or partes[0] != "cpu":
        return None
    try:
        numeros = [int(x) for x in partes[1:]]
    except ValueError:
        return None
    total = sum(numeros)
    idle = numeros[3] + (numeros[4] if len(numeros) > 4 else 0)  # idle + iowait
    return total - idle, total


def parse_wireless(raw: str, interface: str = "wlan0") -> dict | None:
    """`/proc/net/wireless` -> qualidade e sinal do Wi-Fi da interface.

    O arquivo tem duas linhas de cabeçalho e uma por interface:

        wlan0: 0000   58.  -52.  -256        0 ...

    A qualidade (link) e o sinal em dBm vêm com um ponto no fim ("58."), que é
    do formato, não decimal. Sem a interface na lista (cabo, ou Wi-Fi caído)
    devolve None.
    """
    for linha in raw.splitlines():
        linha = linha.strip()
        if not linha.startswith(interface + ":"):
            continue
        campos = linha.split()
        if len(campos) < 4:
            return None
        try:
            link = int(float(campos[2].rstrip(".")))
            sinal_dbm = int(float(campos[3].rstrip(".")))
        except ValueError:
            return None
        return {"interface": interface, "link": link, "sinal_dbm": sinal_dbm}
    return None
