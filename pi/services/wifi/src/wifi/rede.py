"""Configuração de Wi-Fi via NetworkManager (nmcli).

Concentra toda a conversa com o `nmcli`: escanear redes, consultar o estado da
conexão e aplicar novas credenciais. A função `processar()` recebe o comando já
desserializado (dict) que chegou pelo barramento MQTT e devolve um dict de
resposta — sem nunca levantar exceção para fora, porque a origem (o app) é
não-confiável.

Requer que o serviço rode com permissão para mexer no NetworkManager
(normalmente como root; ver o unit systemd robo-wifi.service).
"""

from __future__ import annotations

import logging
import subprocess
from typing import Any

logger = logging.getLogger("wifi.rede")


class ErroRede(Exception):
    """Falha ao executar uma operação de rede (nmcli indisponível/erro)."""


def _run_nmcli(args: list[str], timeout: float = 45.0) -> subprocess.CompletedProcess:
    """Roda `nmcli <args>` capturando saída. Não levanta em returncode != 0."""
    try:
        return subprocess.run(
            ["nmcli", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:  # nmcli não instalado
        raise ErroRede("nmcli não encontrado (NetworkManager instalado?)") from exc
    except subprocess.TimeoutExpired as exc:
        raise ErroRede("tempo esgotado falando com o nmcli") from exc


def _split_terse(linha: str) -> list[str]:
    """Divide uma linha do modo terse do nmcli (`-t`) nos campos.

    O nmcli separa campos por ':' e escapa ':' e '\\' literais com '\\'. Um
    split ingênuo quebraria SSIDs/valores que contêm ':'. Aqui respeitamos o
    escape.
    """
    campos: list[str] = []
    atual: list[str] = []
    i = 0
    while i < len(linha):
        c = linha[i]
        if c == "\\" and i + 1 < len(linha):
            atual.append(linha[i + 1])
            i += 2
            continue
        if c == ":":
            campos.append("".join(atual))
            atual = []
            i += 1
            continue
        atual.append(c)
        i += 1
    campos.append("".join(atual))
    return campos


def escanear_redes(iface: str) -> list[dict[str, Any]]:
    """Lista as redes Wi-Fi visíveis, sem duplicatas, da mais forte p/ a mais fraca."""
    cp = _run_nmcli(
        ["-t", "-f", "SSID,SIGNAL,SECURITY", "device", "wifi", "list", "ifname", iface]
    )
    if cp.returncode != 0:
        raise ErroRede(cp.stderr.strip() or "falha ao escanear redes")

    redes: list[dict[str, Any]] = []
    vistos: set[str] = set()
    for linha in cp.stdout.splitlines():
        if not linha:
            continue
        campos = _split_terse(linha)
        ssid = campos[0] if campos else ""
        if not ssid or ssid in vistos:
            continue  # ignora redes ocultas (SSID vazio) e duplicatas
        vistos.add(ssid)
        sinal = int(campos[1]) if len(campos) > 1 and campos[1].isdigit() else None
        seguranca = campos[2] if len(campos) > 2 and campos[2] else "aberta"
        redes.append({"ssid": ssid, "sinal": sinal, "seguranca": seguranca})

    redes.sort(key=lambda r: r["sinal"] or 0, reverse=True)
    return redes


def status_atual(iface: str) -> dict[str, Any]:
    """Estado atual da interface: conectado?, SSID e IP."""
    cp = _run_nmcli(
        ["-t", "-f", "GENERAL.CONNECTION,IP4.ADDRESS", "device", "show", iface]
    )
    ssid: str | None = None
    ip: str | None = None
    for linha in cp.stdout.splitlines():
        campos = _split_terse(linha)
        if len(campos) < 2:
            continue
        chave, valor = campos[0], campos[1]
        if chave == "GENERAL.CONNECTION":
            ssid = valor if valor not in ("", "--") else None
        elif chave.startswith("IP4.ADDRESS"):
            # Formato "192.168.0.42/24" -> guardamos só o endereço.
            ip = valor.split("/")[0] if valor else None
    return {"conectado": ssid is not None, "ssid": ssid, "ip": ip}


def conectar(ssid: str, senha: str, iface: str) -> dict[str, Any]:
    """Tenta conectar a uma rede; devolve o status resultante ou levanta ErroRede."""
    args = ["-w", "45", "device", "wifi", "connect", ssid, "ifname", iface]
    if senha:
        args += ["password", senha]
    cp = _run_nmcli(args, timeout=60.0)
    if cp.returncode != 0:
        # nmcli manda a causa (senha incorreta, rede fora de alcance...) no stderr.
        raise ErroRede(cp.stderr.strip() or cp.stdout.strip() or "falha ao conectar")
    return status_atual(iface)


def processar(comando: dict[str, Any], iface: str) -> dict[str, Any]:
    """Interpreta o comando de Wi-Fi (dict) e devolve a resposta (dict).

    Ações suportadas (campo "acao", default "conectar"):
        {"acao": "listar"}
        {"acao": "status"}
        {"acao": "conectar", "ssid": "MinhaRede", "senha": "segredo"}
    """
    if not isinstance(comando, dict):
        return {"ok": False, "erro": "formato_invalido"}

    acao = comando.get("acao", "conectar")

    try:
        if acao == "status":
            return {"ok": True, "acao": "status", **status_atual(iface)}

        if acao == "listar":
            return {"ok": True, "acao": "listar", "redes": escanear_redes(iface)}

        if acao == "conectar":
            ssid = comando.get("ssid")
            if not isinstance(ssid, str) or not ssid.strip():
                return {"ok": False, "acao": "conectar", "erro": "ssid_obrigatorio"}
            # Aceita "senha" (pt) ou "password" (en); rede aberta -> "".
            #
            # O `isinstance` não é preciosismo: sem ele, uma senha que chegasse
            # como número ou lista ia direto para a lista de argumentos do
            # `subprocess`, que exige texto — e o `TypeError` resultante não é
            # `ErroRede`, então escapava deste `try` e quebrava a promessa do
            # módulo de nunca levantar para fora.
            senha = comando.get("senha") or comando.get("password") or ""
            if not isinstance(senha, str):
                return {"ok": False, "acao": "conectar", "erro": "senha_invalida"}
            ssid = ssid.strip()
            # Um SSID começando com "-" seria lido pelo `nmcli` como opção, e
            # não como nome de rede. Não é injeção de shell (não há shell aqui),
            # mas é o cliente escolhendo qual comando o robô executa.
            if ssid.startswith("-"):
                return {"ok": False, "acao": "conectar", "erro": "ssid_invalido"}
            resultado = conectar(ssid, senha, iface)
            logger.info("Conectado à rede %r (ip=%s).", ssid, resultado.get("ip"))
            return {"ok": True, "acao": "conectar", "ssid": ssid, **resultado}

        return {"ok": False, "erro": "acao_desconhecida", "acao": acao}

    except ErroRede as exc:
        logger.warning("Falha na ação %r: %s", acao, exc)
        return {"ok": False, "acao": acao, "erro": "falha_rede", "detalhe": str(exc)}
