#!/usr/bin/env bash
#
# Sobe a nuvem da Atlas inteira na sua máquina, e diz o que fazer com ela.
#
#   ./cloud/scripts/ambiente-local.sh              sobe tudo e confere
#   ./cloud/scripts/ambiente-local.sh --robo       sobe e deixa um robô falso publicando
#   ./cloud/scripts/ambiente-local.sh --parar      para os containers (guarda os dados)
#   ./cloud/scripts/ambiente-local.sh --zerar      para e APAGA o banco
#   ./cloud/scripts/ambiente-local.sh --logs       acompanha os logs
#   ./cloud/scripts/ambiente-local.sh --endereco   só mostra o que pôr no app
#   ./cloud/scripts/ambiente-local.sh --rede       libera a porta para o celular
#
# É o MESMO `docker-compose.yml` que vai rodar na VM do LARCC — este script só
# acrescenta `compose.local.yml` por cima, que ajusta três coisas (portas na
# rede local, broker sem senha, sem túnel). Migrar é parar de passar esse
# arquivo. Ver `docs/ambiente-local.md`.
set -euo pipefail

CLOUD_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${CLOUD_DIR}"

# Preenchido por `exigir_docker`: vazio no caso normal, "sudo" logo depois da
# instalação, enquanto a sessão ainda não entrou no grupo docker.
DOCKER_SUDO=""
COMPOSE=()

BOLD=$(tput bold 2>/dev/null || true)
RESET=$(tput sgr0 2>/dev/null || true)

passo() { echo; echo "${BOLD}==> $*${RESET}"; }
info()  { echo "    $*"; }
ok()    { echo "    [ ok ] $*"; }
falha() { echo "    [falha] $*" >&2; }
erro()  { echo; echo "    [erro] $*" >&2; exit 1; }

# --- pré-requisitos ---------------------------------------------------------
exigir_docker() {
    command -v docker >/dev/null || erro "Docker não encontrado.
    Instale com:  sudo ./cloud/scripts/instalar-docker-wsl.sh"

    if ! docker info >/dev/null 2>&1; then
        systemctl is-active --quiet docker 2>/dev/null \
            || erro "o serviço do Docker não está de pé: sudo systemctl start docker"

        # O Docker está rodando e esta sessão não alcança o socket. O caso
        # comum é ter acabado de instalar: o `usermod` já pôs o usuário no
        # grupo, mas o grupo de um processo é decidido quando ele nasce, e este
        # terminal nasceu antes. Em vez de mandar reiniciar o WSL e voltar, o
        # script segue com sudo e avisa — reiniciar continua sendo o certo, só
        # não precisa ser agora.
        if sudo -n docker info >/dev/null 2>&1 || sudo docker info >/dev/null 2>&1; then
            DOCKER_SUDO="sudo"
            falha "esta sessão ainda não está no grupo docker; seguindo com sudo."
            info "para parar de precisar dele: feche o terminal, rode 'wsl --shutdown'"
            info "no PowerShell e abra de novo."
        else
            erro "o Docker está rodando, mas seu usuário não pode falar com ele.
    Falta entrar no grupo: sudo usermod -aG docker \$USER
    Depois, no PowerShell: wsl --shutdown  (e abra o terminal de novo)"
        fi
    fi

    COMPOSE=(${DOCKER_SUDO} docker compose -f docker-compose.yml -f compose.local.yml)
}

# --- endereço que o celular enxerga -----------------------------------------
#
# Os containers sobem dentro do WSL, e o IP do WSL não é o IP da sua máquina na
# rede — a menos que o WSL2 esteja em modo espelhado. Esta função descobre em
# qual dos dois casos estamos, porque a instrução para o celular muda.
descobrir_endereco() {
    IP_LOCAL="$(ip -4 route get 1.1.1.1 2>/dev/null | grep -oP 'src \K[0-9.]+' || echo '')"
    ESPELHADO=false
    # Uma faixa de LAN doméstica/institucional significa que o WSL está usando a
    # interface do Windows (modo espelhado). A faixa 172.x que a NAT do WSL usa
    # não aparece aqui nesse caso.
    if [[ "${IP_LOCAL}" == 192.168.* || "${IP_LOCAL}" == 10.* ]]; then
        ESPELHADO=true
    fi
}

# A rede espelhada resolve o ENDEREÇO, não a PERMISSÃO: com ela o WSL passa a
# ser filtrado pelo firewall do Hyper-V, cuja política de entrada padrão é
# `Block`. Sem uma regra, o celular não entra.
#
# **Esta checagem olha a regra, e não a conexão** — e isso é deliberado. Medido
# nesta máquina: no modo espelhado o próprio Windows NÃO consegue alcançar o
# WSL pelo IP da rede (nem pelo da LAN, nem pelo do Tailscale); só por
# 127.0.0.1. É uma limitação do espelhamento, não um bloqueio. Uma versão
# anterior desta função tentava conectar dali e concluía "bloqueado" com tudo
# funcionando — um alarme falso que mandaria procurar problema onde não há.
#
# Quem responde de verdade é o celular. O que dá para afirmar daqui é se a
# permissão existe.
conferir_firewall_wsl() {
    command -v powershell.exe >/dev/null 2>&1 || return 0

    local regras
    regras="$(powershell.exe -NoProfile -Command \
        "(Get-NetFirewallHyperVRule -ErrorAction SilentlyContinue | Where-Object { \$_.Name -like 'Atlas*' -and \$_.Action -eq 'Allow' }).Count" \
        2>/dev/null | tr -d '\r\n ')"

    if [[ "${regras}" =~ ^[0-9]+$ ]] && (( regras > 0 )); then
        ok "firewall do Hyper-V liberado (${regras} regra(s) Atlas)"
        info "o teste final é abrir o app no celular — daqui não dá para provar:"
        info "no modo espelhado o próprio Windows não alcança o WSL pelo IP da rede."
        return 0
    fi

    echo
    falha "não há regra liberando a porta: o celular não vai alcançar."
    info "com a rede espelhada, quem filtra o WSL é o firewall do Hyper-V, e a"
    info "política de entrada padrão dele é Block. Resolve daqui mesmo, com:"
    info ""
    info "    ./cloud/scripts/ambiente-local.sh --rede"
    info ""
    info "(vai abrir o pedido de permissão do Windows — é a única parte que não"
    info "dá para fazer do Linux. Ver docs/ambiente-local.md.)"
}

mostrar_endereco() {
    descobrir_endereco
    TOKEN="$(grep -E '^API_TOKEN=' .env 2>/dev/null | cut -d= -f2- || echo '')"

    echo
    echo "${BOLD}Para configurar no app (Dados do robô → engrenagem):${RESET}"
    echo
    if [[ "${ESPELHADO}" == true ]]; then
        echo "    Endereço:  http://${IP_LOCAL}:8000"
        echo "    Token:     ${TOKEN}"
        echo
        info "o celular precisa estar no mesmo Wi-Fi que esta máquina"
        conferir_firewall_wsl
    else
        echo "    Endereço:  http://${IP_LOCAL}:8000     ${BOLD}(só de dentro do WSL)${RESET}"
        echo "    Token:     ${TOKEN}"
        echo
        falha "este IP é da rede interna do WSL: o celular NÃO alcança."
        cat <<EOF

    Para o celular chegar até aqui, ligue a rede espelhada do WSL2. No
    Windows, crie ou edite o arquivo  C:\\Users\\SEU_USUARIO\\.wslconfig:

        [wsl2]
        networkingMode=mirrored

    Depois, no PowerShell:  wsl --shutdown  — e abra o terminal de novo.
    A partir daí o WSL passa a usar a mesma placa de rede do Windows, e o
    endereço acima vira o IP da sua máquina na rede.

    (Detalhes e a alternativa sem rede espelhada: docs/ambiente-local.md)
EOF
    fi
}

# --- ações -------------------------------------------------------------------
case "${1:-}" in
    --parar)
        exigir_docker
        passo "Parando (os dados ficam guardados)"
        "${COMPOSE[@]}" down
        info "para subir de novo: $0"
        exit 0
        ;;
    --zerar)
        exigir_docker
        echo "${BOLD}Isto APAGA o banco de testes local.${RESET} O da VM não é tocado."
        read -r -p "    Digite 'zerar' para confirmar: " resposta
        [[ "${resposta}" == "zerar" ]] || erro "cancelado"
        "${COMPOSE[@]}" down -v
        info "volumes removidos; a próxima subida começa do zero"
        exit 0
        ;;
    --logs)
        exigir_docker
        exec "${COMPOSE[@]}" logs -f --tail=50
        ;;
    --endereco)
        mostrar_endereco
        exit 0
        ;;
    --rede)
        # Liberar a porta para o celular exige privilégio do Windows, que o
        # sudo do Linux não concede. O script abaixo pede a elevação (o UAC) e
        # escolhe entre regra de firewall e encaminhamento conforme o modo de
        # rede do WSL — errar essa escolha é meia hora procurando problema na API.
        exec "${CLOUD_DIR}/scripts/liberar-rede.sh" "${2:-}"
        ;;
    --robo|"")
        ;;
    -h|--help)
        sed -n '2,18p' "$0"
        exit 0
        ;;
    *)
        erro "opção desconhecida: $1 (use --help)"
        ;;
esac

# --- 1. configuração --------------------------------------------------------
exigir_docker

passo "[1/5] Configuração"
if [[ ! -f .env ]]; then
    cp .env.local.example .env
    info ".env criado a partir de .env.local.example (senhas de teste, já prontas)"
else
    info ".env já existe; mantendo"
    # Um .env de produção aqui subiria containers com as senhas da VM na sua
    # máquina, e — pior — faria o app apontar para o token errado sem avisar.
    #
    # O marcador, e não o token: trocar o `API_TOKEN` de teste por um sorteado
    # é coisa recomendada de se fazer assim que a API sai para a internet pelo
    # túnel, e um aviso que dispara justamente aí ensinaria a ignorá-lo.
    if ! grep -q "^# ATLAS_AMBIENTE=local" .env; then
        info "[aviso] este .env não parece o de teste; conferindo mesmo assim"
    fi
fi

# --- 2. subir ---------------------------------------------------------------
passo "[2/5] Subindo os containers"
info "a primeira vez baixa as imagens e compila a API — pode levar alguns minutos"
"${COMPOSE[@]}" up -d --build

# --- 3. esperar a API ficar de pé -------------------------------------------
passo "[3/5] Esperando a API responder"
API_OK=false
for tentativa in $(seq 1 60); do
    # `--fail` faz o curl devolver erro em 5xx: a API responde 503 enquanto o
    # banco não aceita conexão, e é justamente isso que estamos esperando passar.
    if curl -fsS http://127.0.0.1:8000/saude >/dev/null 2>&1; then
        API_OK=true
        break
    fi
    sleep 2
    [[ $((tentativa % 10)) -eq 0 ]] && info "ainda subindo… (${tentativa}0s)"
done

if [[ "${API_OK}" != true ]]; then
    falha "a API não respondeu em dois minutos."
    info "veja o que aconteceu com:  $0 --logs"
    exit 1
fi
ok "API no ar"

# --- 4. conferir cada peça --------------------------------------------------
passo "[4/5] Conferindo as peças"

SAUDE="$(curl -fsS http://127.0.0.1:8000/saude)"
echo "${SAUDE}" | grep -q '"banco":true' && ok "banco respondendo" \
    || { falha "a API está de pé mas não fala com o banco: ${SAUDE}"; exit 1; }
echo "${SAUDE}" | grep -q '"token_configurado":true' && ok "token configurado" \
    || falha "API_TOKEN vazio no .env — as rotas do app vão recusar tudo"

TOKEN="$(grep -E '^API_TOKEN=' .env | cut -d= -f2-)"
CODIGO="$(curl -s -o /dev/null -w '%{http_code}' \
    -H "Authorization: Bearer ${TOKEN}" http://127.0.0.1:8000/v1/estado)"
[[ "${CODIGO}" == "200" ]] && ok "token aceito pelas rotas do app" \
    || falha "as rotas do app responderam ${CODIGO} (esperado 200)"

for servico in mosquitto timescaledb ingestor api; do
    ESTADO="$("${COMPOSE[@]}" ps --format '{{.Service}} {{.State}}' 2>/dev/null \
        | awk -v s="${servico}" '$1==s{print $2}')"
    [[ "${ESTADO}" == "running" ]] && ok "${servico}" || falha "${servico}: ${ESTADO:-ausente}"
done

# --- 5. dados ---------------------------------------------------------------
passo "[5/5] Dados"
TOTAL="$(curl -fsS http://127.0.0.1:8000/v1/publico/resumo \
    | grep -o '"total":[0-9]*' | cut -d: -f2 | paste -sd+ | bc 2>/dev/null || echo 0)"

if [[ "${TOTAL:-0}" -gt 0 ]]; then
    ok "${TOTAL} mensagens já no banco"
else
    info "banco vazio; gerando seis horas de histórico para as telas terem o que mostrar"
    if ${DOCKER_SUDO} docker run --rm --network=host -v "${CLOUD_DIR}/scripts:/s:ro" \
        -e PGHOST=127.0.0.1 \
        -e PGUSER="$(grep -E '^PGUSER=' .env | cut -d= -f2-)" \
        -e PGPASSWORD="$(grep -E '^PGPASSWORD=' .env | cut -d= -f2-)" \
        -e PGDATABASE="$(grep -E '^PGDATABASE=' .env | cut -d= -f2-)" \
        python:3.12-slim sh -c 'pip install -q "psycopg[binary]" && python /s/semear-demonstracao.py --horas 6' \
        >/dev/null 2>&1; then
        ok "histórico gerado"
    else
        falha "não consegui semear automaticamente."
        info "rode à mão:  pip install 'psycopg[binary]' && \\"
        info "  PGHOST=127.0.0.1 PGPASSWORD=... python3 cloud/scripts/semear-demonstracao.py"
    fi
fi

mostrar_endereco

cat <<EOF

${BOLD}Comandos que você vai querer:${RESET}

    $0 --logs        o que cada peça está fazendo
    $0 --rede        libera a porta para o celular alcançar
    $0 --parar       desliga (os dados ficam)
    $0 --zerar       desliga e apaga o banco

    python3 cloud/scripts/robo-falso.py    um robô publicando ao vivo,
                                           pelo caminho inteiro (MQTT → app)

EOF

# --- robô falso, se pedido --------------------------------------------------
if [[ "${1:-}" == "--robo" ]]; then
    passo "Robô falso publicando (Ctrl-C encerra)"
    exec python3 "${CLOUD_DIR}/scripts/robo-falso.py"
fi
