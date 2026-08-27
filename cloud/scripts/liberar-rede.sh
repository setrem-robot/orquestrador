#!/usr/bin/env bash
#
# Deixa o celular alcançar o ambiente de testes. Rode daqui mesmo, do Linux:
#
#   ./cloud/scripts/liberar-rede.sh              libera as portas
#   ./cloud/scripts/liberar-rede.sh --remover    desfaz
#   ./cloud/scripts/liberar-rede.sh --conferir   só diz como está
#
# Vai aparecer o "Deseja permitir que este aplicativo faça alterações?" do
# Windows — clique em Sim. É a única parte que não dá para fazer do Linux:
# mexer em firewall exige privilégio de administrador do Windows, e ele só é
# concedido por esse clique.
#
# POR QUE ISTO EXISTE
#
# Os containers rodam dentro do WSL, e o WSL não é alcançável de fora sem uma
# permissão explícita. O que precisa ser feito depende do modo de rede — regra
# no firewall do Hyper-V, ou encaminhamento de porta — e escolher errado é
# passar meia hora achando que o problema é a API. Este script descobre em qual
# modo você está e chama o caminho certo.
set -euo pipefail

CLOUD_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PS1_LINUX="${CLOUD_DIR}/scripts/liberar-portas-wsl.ps1"

BOLD=$(tput bold 2>/dev/null || true)
RESET=$(tput sgr0 2>/dev/null || true)

passo() { echo; echo "${BOLD}==> $*${RESET}"; }
info()  { echo "    $*"; }
ok()    { echo "    [ ok ] $*"; }
falha() { echo "    [falha] $*" >&2; }
erro()  { echo; echo "    [erro] $*" >&2; exit 1; }

command -v powershell.exe >/dev/null 2>&1 \
    || erro "isto só faz sentido no WSL — num Linux de verdade não há firewall
    do Windows no caminho. Na VM do LARCC, quem publica a API é o túnel da
    Cloudflare (ver docs/setup-cloud.md)."

IP_LOCAL="$(ip -4 route get 1.1.1.1 2>/dev/null | grep -oP 'src \K[0-9.]+' || echo '')"
ESPELHADO=false
[[ "${IP_LOCAL}" == 192.168.* || "${IP_LOCAL}" == 10.* ]] && ESPELHADO=true

# --- só conferir -------------------------------------------------------------
if [[ "${1:-}" == "--conferir" ]]; then
    passo "Como está agora"
    if [[ "${ESPELHADO}" == true ]]; then
        info "modo de rede: espelhado (o WSL usa o IP da máquina: ${IP_LOCAL})"
        REGRAS="$(powershell.exe -NoProfile -Command \
            "(Get-NetFirewallHyperVRule -ErrorAction SilentlyContinue | Where-Object { \$_.Name -like 'Atlas*' -and \$_.Action -eq 'Allow' }).Count" \
            2>/dev/null | tr -d '\r\n ')"
        if [[ "${REGRAS}" =~ ^[0-9]+$ ]] && (( REGRAS > 0 )); then
            ok "${REGRAS} regra(s) no firewall do Hyper-V"
        else
            falha "nenhuma regra — rode este script sem argumentos"
        fi
        info ""
        info "Daqui não dá para provar que o celular alcança: no modo espelhado"
        info "o próprio Windows não fala com o WSL pelo IP da rede. O teste é o app."
    else
        info "modo de rede: NAT (o WSL tem rede própria: ${IP_LOCAL})"
        powershell.exe -NoProfile -Command "netsh interface portproxy show v4tov4" 2>/dev/null \
            | tr -d '\r' | sed 's/^/    /' | head -12
    fi
    exit 0
fi

# --- aplicar (ou remover) ----------------------------------------------------
passo "Modo de rede do WSL"
if [[ "${ESPELHADO}" == true ]]; then
    info "espelhado — o WSL usa o IP da máquina (${IP_LOCAL})"
    info "o que falta é uma regra no firewall do Hyper-V"
else
    info "NAT — o WSL tem uma rede própria (${IP_LOCAL})"
    info "o que falta é o Windows escutar na porta e encaminhar para cá"
fi

# `wslpath -w` porque quem vai abrir o arquivo é o PowerShell, do lado do
# Windows: um caminho /mnt/d/... não existe para ele.
PS1_WIN="$(wslpath -w "${PS1_LINUX}")"
ARGS="'-NoProfile','-ExecutionPolicy','Bypass','-File','${PS1_WIN}'"
[[ "${1:-}" == "--remover" ]] && ARGS="${ARGS},'-Remover'"

passo "Pedindo permissão de administrador"
info "vai abrir uma janela do Windows perguntando se você permite — clique em Sim."
info "(sem isso não dá: mexer em firewall exige esse privilégio, e ele só vem"
info "desse clique — nem o sudo do Linux o concede)"

# `-Verb RunAs` é o que dispara o UAC. A janela é própria e fica aberta até
# alguém apertar Enter (ver `Pausar`, no .ps1) — senão ela some antes de dar
# tempo de ler o resultado.
powershell.exe -NoProfile -Command \
    "Start-Process powershell -Verb RunAs -ArgumentList ${ARGS}" >/dev/null 2>&1 \
    || erro "não consegui abrir a janela elevada. Você pode fazer à mão:
    abra o PowerShell como administrador e rode:
      powershell -ExecutionPolicy Bypass -File ${PS1_WIN}"

passo "Aguardando"
info "termine na janela que abriu (ela mostra o resultado e espera um Enter)."
echo
read -r -p "    Quando ela fechar, aperte Enter aqui para eu conferir: " _ || true

passo "Conferindo do lado do Linux"
if [[ "${1:-}" == "--remover" ]]; then
    info "regras removidas; o celular deixa de alcançar esta máquina"
    exit 0
fi

exec "${BASH_SOURCE[0]}" --conferir
