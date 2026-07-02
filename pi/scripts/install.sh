#!/usr/bin/env bash
#
# Instala TODOS os serviços do Pi num único virtualenv compartilhado e
# registra cada serviço no systemd (requer sudo).
#
# Um venv só para todos os serviços faz sentido aqui: eles compartilham a lib
# robo-common e rodam na mesma máquina. A ordem importa: o robo-common (lib
# compartilhada) é instalado em modo editável PRIMEIRO; assim, quando
# instalamos cada serviço, o pip já encontra a dependência satisfeita e não
# tenta baixá-la do PyPI.
#
# USO (no Raspberry Pi, a partir da raiz do repo):
#   ./pi/scripts/install.sh
#
# Rode novamente sempre que mudar dependências ou adicionar um serviço.
#
# Dependências de sistema necessárias (instale antes de rodar este script):
#   sudo apt install python3-gpiozero python3-lgpio -y   # para o serviço motores

set -euo pipefail

# Raiz de "pi/" (uma pasta acima deste script).
PI_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMMON_DIR="$PI_ROOT/services/_common"
VENV="$PI_ROOT/.venv"
SYSTEMD_DIR="$PI_ROOT/systemd"

# Serviços a instalar (pastas em pi/services/). Acrescente novos serviços aqui.
SERVICOS=(serial_ingestor orquestrador gps wifi motores)

# Mapa serviço → nome do arquivo .service (sem extensão).
declare -A SYSTEMD_SERVICES=(
    [serial_ingestor]="robo-serial-ingestor"
    [orquestrador]="robo-orquestrador"
    [gps]="robo-gps"
    [wifi]="robo-wifi"
    [motores]="robo-motores"
)

echo ">> Criando virtualenv compartilhado em $VENV"
python3 -m venv "$VENV"

echo ">> Atualizando pip"
"$VENV/bin/pip" install --upgrade pip >/dev/null

echo ">> Instalando robo-common (editável)"
"$VENV/bin/pip" install -e "$COMMON_DIR"

for servico in "${SERVICOS[@]}"; do
  echo ">> Instalando $servico (editável)"
  "$VENV/bin/pip" install -e "$PI_ROOT/services/$servico"
done

echo ""
echo "Pronto. Entrypoints disponíveis em $VENV/bin/ :"
for servico in "${SERVICOS[@]}"; do
  # O entrypoint usa hífen no lugar do underscore (ver pyproject de cada um).
  echo "   ${servico//_/-}"
done

# ------------------------------------------------------------------
# Instalação no systemd (só executa se o systemd estiver disponível)
# ------------------------------------------------------------------
if command -v systemctl &>/dev/null && [ -d /etc/systemd/system ]; then
    echo ""
    echo ">> Instalando serviços no systemd (requer sudo)..."
    for servico in "${SERVICOS[@]}"; do
        svc="${SYSTEMD_SERVICES[$servico]:-}"
        [ -z "$svc" ] && continue
        src="$SYSTEMD_DIR/$svc.service"
        if [ ! -f "$src" ]; then
            echo "   AVISO: $src não encontrado; pulando."
            continue
        fi
        dst="/etc/systemd/system/$svc.service"
        # Substitui o caminho fixo do repo pelo caminho real detectado em tempo de install.
        sed "s|/home/setrem/setrem-robot/pi|$PI_ROOT|g" "$src" | sudo tee "$dst" >/dev/null
        echo "   Instalado: $dst"
    done

    sudo systemctl daemon-reload

    echo ">> Habilitando e iniciando serviços..."
    for servico in "${SERVICOS[@]}"; do
        svc="${SYSTEMD_SERVICES[$servico]:-}"
        [ -z "$svc" ] && continue
        sudo systemctl enable --now "$svc"
        echo "   $svc: habilitado e iniciado."
    done
else
    echo ""
    echo "systemd não detectado; pulando instalação dos serviços."
    echo "Para instalar manualmente:"
    echo "   sudo cp $SYSTEMD_DIR/<servico>.service /etc/systemd/system/"
    echo "   sudo systemctl daemon-reload && sudo systemctl enable --now <servico>"
fi

echo ""
echo "Lembre-se: para acessar a serial (ESP32 e GPS) o usuário precisa estar"
echo "no grupo 'dialout':"
echo "   sudo usermod -aG dialout \$USER   (e reabrir a sessão)"
