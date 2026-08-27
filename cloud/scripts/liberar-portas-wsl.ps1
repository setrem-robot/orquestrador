# Deixa o celular (e o Raspberry Pi) alcançarem o ambiente de testes no WSL.
#
# Você normalmente NÃO roda este arquivo à mão. Quem o chama é
# `./cloud/scripts/liberar-rede.sh`, do próprio Linux, que descobre o modo de
# rede e pede a elevação. Rodar direto também funciona, no PowerShell como
# administrador:
#
#     powershell -ExecutionPolicy Bypass -File .\cloud\scripts\liberar-portas-wsl.ps1
#     powershell -ExecutionPolicy Bypass -File .\cloud\scripts\liberar-portas-wsl.ps1 -Remover
#
# ------------------------------------------------------------------------
# SÃO DOIS CAMINHOS, E O CERTO DEPENDE DO MODO DE REDE DO WSL
#
# **Espelhado** (`networkingMode=mirrored` no `.wslconfig`): o WSL usa a mesma
# placa de rede do Windows, e passa a ser filtrado pelo **firewall do Hyper-V**
# — um conjunto de regras separado do firewall comum, com política de entrada
# `Block` por padrão. O cmdlet certo é `New-NetFirewallHyperVRule`.
#
# `New-NetFirewallRule` (o que aparece em quase toda resposta de internet) não
# resolve nesse modo: cria a regra no firewall do Windows, e quem bloqueia é o
# do Hyper-V.
#
# **NAT** (o padrão, sem `.wslconfig`): o WSL tem uma rede própria e um IP que
# só existe lá dentro. Aí é preciso o Windows escutar na porta e encaminhar
# (`netsh portproxy`), mais uma regra no firewall comum. Funciona bem, com um
# porém: o IP do WSL muda a cada reinício, e o encaminhamento passa a apontar
# para o vazio — daí este script poder ser rodado de novo para reapontar.
# ------------------------------------------------------------------------
param(
    [switch]$Remover
)

$ErrorActionPreference = 'Stop'

# Identificador fixo do WSL no firewall do Hyper-V. Confira com
# `Get-NetFirewallHyperVVMCreator` se um dia deixar de bater.
$WslId = '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}'

$Portas = @(
    @{ Nome = 'Atlas-API';       Porta = 8000; Descricao = 'API de telemetria — o app lê por aqui' },
    @{ Nome = 'Atlas-Mosquitto'; Porta = 1883; Descricao = 'Broker MQTT — para apontar o Raspberry Pi para cá' }
)

function Ehadmin {
    $identidade = [Security.Principal.WindowsIdentity]::GetCurrent()
    (New-Object Security.Principal.WindowsPrincipal $identidade).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Pausar {
    # A janela elevada é própria: sem isto ela fecha antes de alguém ler o que
    # deu, e o único jeito de saber o resultado seria adivinhar.
    Write-Host ''
    Write-Host '  (pressione Enter para fechar)' -ForegroundColor DarkGray
    [void](Read-Host)
}

if (-not (Ehadmin)) {
    Write-Host ''
    Write-Host '  Este script precisa de PowerShell como Administrador.' -ForegroundColor Red
    Write-Host '  Do Linux, use:  ./cloud/scripts/liberar-rede.sh' -ForegroundColor Yellow
    Write-Host ''
    exit 1
}

# --- em que modo o WSL está? -------------------------------------------------
# No espelhado o WSL enxerga o IP da máquina na rede; no NAT, um 172.x próprio.
$ipWsl = (wsl.exe -- hostname -I 2>$null) -split '\s+' | Where-Object { $_ -match '^\d+\.' }
$ipLan = (Get-NetIPAddress -AddressFamily IPv4 |
          Where-Object { $_.IPAddress -like '192.168.*' -or $_.IPAddress -like '10.*' } |
          Select-Object -First 1 -ExpandProperty IPAddress)
$Espelhado = $ipWsl -contains $ipLan

Write-Host ''
Write-Host '==> Modo de rede do WSL' -ForegroundColor Cyan
if ($Espelhado) {
    Write-Host "    espelhado (o WSL usa o IP da máquina: $ipLan)"
} else {
    $ipInterno = ($ipWsl | Select-Object -First 1)
    Write-Host "    NAT (o WSL tem rede própria: $ipInterno)"
}

# --- remover -----------------------------------------------------------------
if ($Remover) {
    Write-Host ''
    Write-Host '==> Removendo o que este script cria' -ForegroundColor Cyan
    foreach ($item in $Portas) {
        Remove-NetFirewallHyperVRule -Name $item.Nome -ErrorAction SilentlyContinue
        Get-NetFirewallRule -DisplayName "$($item.Nome) (WSL)" -ErrorAction SilentlyContinue |
            Remove-NetFirewallRule -ErrorAction SilentlyContinue
        netsh interface portproxy delete v4tov4 listenport=$($item.Porta) listenaddress=0.0.0.0 2>&1 | Out-Null
        Write-Host "    limpo: $($item.Nome) (porta $($item.Porta))"
    }
    Write-Host ''
    Pausar
    exit 0
}

# --- aplicar -----------------------------------------------------------------
Write-Host ''
Write-Host '==> Liberando as portas' -ForegroundColor Cyan

foreach ($item in $Portas) {
    if ($Espelhado) {
        # Idempotente: rodar duas vezes não duplica nem falha.
        Remove-NetFirewallHyperVRule -Name $item.Nome -ErrorAction SilentlyContinue
        New-NetFirewallHyperVRule `
            -Name $item.Nome `
            -DisplayName "$($item.Nome) (WSL)" `
            -VMCreatorId $WslId `
            -Direction Inbound `
            -Protocol TCP `
            -LocalPorts $item.Porta `
            -Action Allow | Out-Null
        Write-Host "    [ ok ] $($item.Porta)  regra no firewall do Hyper-V  ·  $($item.Descricao)"
    } else {
        $destino = ($ipWsl | Select-Object -First 1)
        netsh interface portproxy delete v4tov4 listenport=$($item.Porta) listenaddress=0.0.0.0 2>&1 | Out-Null
        netsh interface portproxy add v4tov4 `
            listenport=$($item.Porta) listenaddress=0.0.0.0 `
            connectport=$($item.Porta) connectaddress=$destino 2>&1 | Out-Null

        Get-NetFirewallRule -DisplayName "$($item.Nome) (WSL)" -ErrorAction SilentlyContinue |
            Remove-NetFirewallRule -ErrorAction SilentlyContinue
        New-NetFirewallRule `
            -DisplayName "$($item.Nome) (WSL)" `
            -Direction Inbound `
            -Protocol TCP `
            -LocalPort $item.Porta `
            -Action Allow | Out-Null
        Write-Host "    [ ok ] $($item.Porta)  encaminhando para $destino  ·  $($item.Descricao)"
    }
}

# --- conferir ----------------------------------------------------------------
Write-Host ''
Write-Host '==> Conferindo' -ForegroundColor Cyan

if ($Espelhado) {
    # Não adianta tentar conectar daqui: no modo espelhado o próprio Windows
    # não alcança o WSL pelo IP da rede (medido: nem pelo da LAN, nem por um do
    # Tailscale; só por 127.0.0.1). Isso é do espelhamento, não do firewall —
    # um teste aqui daria timeout e faria parecer que nada funcionou.
    $regras = (Get-NetFirewallHyperVRule -ErrorAction SilentlyContinue |
               Where-Object { $_.Name -like 'Atlas*' -and $_.Action -eq 'Allow' }).Count
    Write-Host "    $regras regra(s) ativa(s) no firewall do Hyper-V"
    Write-Host ''
    Write-Host '    Daqui não dá para provar que funcionou: no modo espelhado o' -ForegroundColor DarkGray
    Write-Host '    Windows não alcança o WSL pelo IP da rede. Quem responde é o' -ForegroundColor DarkGray
    Write-Host '    celular.' -ForegroundColor DarkGray
} else {
    try {
        $r = Invoke-WebRequest -Uri "http://${ipLan}:8000/saude" -TimeoutSec 8 -UseBasicParsing
        Write-Host "    [ ok ] http://${ipLan}:8000/saude respondeu $($r.StatusCode)" -ForegroundColor Green
    } catch {
        Write-Host "    [falha] http://${ipLan}:8000/saude não respondeu." -ForegroundColor Red
        Write-Host '    As regras foram criadas; falta o ambiente estar de pé.' -ForegroundColor Yellow
        Write-Host '    No Linux:  ./cloud/scripts/ambiente-local.sh' -ForegroundColor Yellow
    }
}

Write-Host ''
Write-Host '  No app, em "Dados do robô" -> engrenagem:' -ForegroundColor Green
Write-Host "      Endereço:  http://${ipLan}:8000"
Write-Host '      Token:     atlas-token-de-teste-local'
Write-Host ''
Write-Host '  O celular precisa estar no mesmo Wi-Fi que esta máquina.'
Pausar
