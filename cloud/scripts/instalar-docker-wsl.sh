#!/usr/bin/env bash
#
# Instala o Docker Engine dentro do Ubuntu do WSL2.
#
#   sudo ./cloud/scripts/instalar-docker-wsl.sh
#
# POR QUE NÃO O DOCKER DESKTOP
#
# O Docker Desktop é uma aplicação de janela que instala a própria máquina
# virtual, ocupa memória o tempo todo e exige licença paga para empresas acima
# de certo porte. Nada disso é necessário aqui: o WSL2 já É uma máquina Linux, e
# o Docker Engine roda dentro dela como rodaria na VM do LARCC.
#
# E essa é a vantagem que importa para este projeto: o que funciona aqui é o
# mesmo `docker compose` que vai rodar lá. Não há "funciona na minha máquina"
# possível quando as duas máquinas são a mesma coisa.
#
# Requisito: systemd ligado no WSL. Já está (`/etc/wsl.conf` tem
# `systemd=true`); sem ele o serviço do Docker não sobe sozinho e seria preciso
# iniciar o daemon à mão a cada abertura do terminal.
set -euo pipefail

BOLD=$(tput bold 2>/dev/null || true)
RESET=$(tput sgr0 2>/dev/null || true)

passo() { echo; echo "${BOLD}==> $*${RESET}"; }
info()  { echo "    $*"; }
erro()  { echo "    [erro] $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || erro "rode com sudo: sudo $0"

# `SUDO_USER` é quem chamou o sudo — é essa conta que precisa entrar no grupo
# docker, e não o root.
USUARIO="${SUDO_USER:-$USER}"
[[ "${USUARIO}" != "root" ]] || erro "rode com sudo a partir do seu usuário, não como root direto"

passo "[1/5] Conferindo o ambiente"
if ! grep -qi microsoft /proc/version; then
    info "isto não parece ser WSL — o script funciona igual num Ubuntu comum, seguindo"
fi
if [[ "$(ps -p 1 -o comm=)" != "systemd" ]]; then
    erro "o systemd não está no PID 1. Ponha 'systemd=true' na seção [boot] de
    /etc/wsl.conf, rode 'wsl --shutdown' no PowerShell e abra o terminal de novo."
fi
info "systemd ok"

if command -v docker >/dev/null && docker compose version >/dev/null 2>&1; then
    info "Docker já instalado: $(docker --version)"
    info "nada a fazer; pulando para a checagem final"
else
    passo "[2/5] Repositório oficial do Docker"
    # O `docker.io` do Ubuntu costuma estar velho e não traz o `docker compose`
    # v2 como subcomando — só o `docker-compose` antigo, com sintaxe diferente
    # do que este projeto usa. Daí o repositório oficial.
    apt-get update -qq
    apt-get install -y -qq ca-certificates curl gnupg
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
        | gpg --dearmor -o /etc/apt/keyrings/docker.gpg --yes
    chmod a+r /etc/apt/keyrings/docker.gpg

    # `VERSION_CODENAME` do Ubuntu; se for uma versão que o Docker ainda não
    # publicou, o `UBUNTU_CODENAME` de fallback evita um 404 no apt update.
    . /etc/os-release
    CODENAME="${UBUNTU_CODENAME:-${VERSION_CODENAME}}"
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu ${CODENAME} stable" \
        > /etc/apt/sources.list.d/docker.list

    passo "[3/5] Instalando"
    apt-get update -qq
    apt-get install -y -qq docker-ce docker-ce-cli containerd.io \
        docker-buildx-plugin docker-compose-plugin
    info "$(docker --version)"
fi

passo "[4/5] Deixando o Docker de pé"
systemctl enable --now docker
systemctl is-active --quiet docker || erro "o serviço do docker não subiu; veja: journalctl -u docker"
info "serviço ativo"

# Sem isto, todo comando docker precisaria de sudo — e o `docker compose` do
# projeto passaria a criar arquivos pertencentes ao root dentro do repositório.
if ! id -nG "${USUARIO}" | grep -qw docker; then
    usermod -aG docker "${USUARIO}"
    info "usuário ${USUARIO} adicionado ao grupo docker"
    NOVO_GRUPO=true
else
    info "usuário ${USUARIO} já está no grupo docker"
    NOVO_GRUPO=false
fi

passo "[5/5] Testando"
if docker run --rm hello-world >/dev/null 2>&1; then
    info "o Docker está funcionando"
else
    info "[aviso] o teste falhou; se for por permissão, é o grupo (veja abaixo)"
fi

echo
echo "${BOLD}Pronto.${RESET}"
if [[ "${NOVO_GRUPO}" == true ]]; then
    cat <<EOF

${BOLD}Falta um passo, e ele é seu:${RESET} entrar no grupo novo. O grupo de um
processo é decidido quando ele nasce, então o terminal aberto agora continua
sem enxergar o docker. No PowerShell do Windows:

    wsl --shutdown

E abra o terminal de novo. Confira com:

    docker run --rm hello-world
EOF
fi

cat <<EOF

Depois disso, suba o ambiente de testes com:

    ./cloud/scripts/ambiente-local.sh

EOF
