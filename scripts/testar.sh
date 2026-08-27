#!/usr/bin/env bash
#
# Roda todos os testes do repositório de uma vez.
#
#   ./scripts/testar.sh            tudo
#   ./scripts/testar.sh pi         só os serviços do Raspberry Pi
#   ./scripts/testar.sh api        só a API da nuvem
#
# NÃO INSTALA NADA NA SUA MÁQUINA. Tudo roda dentro de um container Python
# descartável, com o repositório montado. Isso existe por dois motivos:
#
#   1. A versão do Python da sua distro não é a do robô, e um teste que passa
#      aqui e falha lá é pior do que teste nenhum.
#   2. Instalar cinco pacotes editáveis no Python do sistema é o tipo de coisa
#      que quebra outra coisa três semanas depois, longe da causa.
#
# Se você não tem Docker, o README mostra o caminho com venv à mão — dá no
# mesmo, só é mais passo.
set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGEM="python:3.12-slim"
ALVO="${1:-tudo}"

BOLD=$(tput bold 2>/dev/null || true)
RESET=$(tput sgr0 2>/dev/null || true)
VERDE=$(tput setaf 2 2>/dev/null || true)
VERMELHO=$(tput setaf 1 2>/dev/null || true)

passo() { echo; echo "${BOLD}==> $*${RESET}"; }
ok()    { echo "    ${VERDE}[ ok ]${RESET} $*"; }
falha() { echo "    ${VERMELHO}[falha]${RESET} $*" >&2; }

command -v docker >/dev/null 2>&1 || {
    echo "Docker não encontrado."
    echo "Instale com  sudo ./cloud/scripts/instalar-docker-wsl.sh"
    echo "ou rode os testes à mão — o README mostra como."
    exit 1
} >&2

FALHOU=0

# --- serviços do Pi ---------------------------------------------------------
# Um container só para os dois: `_common` é dependência de ambos, e instalá-la
# duas vezes seria dobrar o tempo por nada.
testar_pi() {
    passo "Serviços do Raspberry Pi"
    docker run --rm -v "${RAIZ}/pi/services":/w -w /w "${IMAGEM}" sh -c '
        set -e
        pip install -q --root-user-action=ignore --disable-pip-version-check -e ./_common pytest
        for s in motores orquestrador gps wifi serial_ingestor; do
            [ -d "$s/tests" ] || continue
            pip install -q --root-user-action=ignore --disable-pip-version-check -e "./$s"
            echo "--- $s ---"
            (cd "$s" && python -m pytest -q)
        done
    ' && ok "serviços do Pi" || { falha "serviços do Pi"; FALHOU=1; }
}

# --- API da nuvem -----------------------------------------------------------
testar_api() {
    passo "API da nuvem"
    docker run --rm -v "${RAIZ}/cloud/api":/w -w /w "${IMAGEM}" sh -c '
        set -e
        pip install -q --root-user-action=ignore --disable-pip-version-check -r requirements.txt pytest
        python -m pytest -q tests/
    ' && ok "API da nuvem" || { falha "API da nuvem"; FALHOU=1; }
}

case "${ALVO}" in
    tudo) testar_pi; testar_api ;;
    pi)   testar_pi ;;
    api)  testar_api ;;
    *)    echo "alvo desconhecido: ${ALVO} (use: tudo | pi | api)" >&2; exit 1 ;;
esac

echo
if [[ ${FALHOU} -eq 0 ]]; then
    echo "${VERDE}${BOLD}Tudo passou.${RESET}"
else
    echo "${VERMELHO}${BOLD}Alguma coisa falhou — o log acima diz o quê.${RESET}"
fi
exit ${FALHOU}
