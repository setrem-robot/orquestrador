# Deixa o celular (e o Raspberry Pi) alcançarem o ambiente de testes no WSL.
#
# Rode no PowerShell COMO ADMINISTRADOR:
#
#     powershell -ExecutionPolicy Bypass -File .\cloud\scripts\liberar-portas-wsl.ps1
#
# Para desfazer:
#
#     powershell -ExecutionPolicy Bypass -File .\cloud\scripts\liberar-portas-wsl.ps1 -Remover
#
# ------------------------------------------------------------------------
# POR QUE ISTO É NECESSÁRIO, E POR QUE `New-NetFirewallRule` NÃO RESOLVE
#
# Com `networkingMode=mirrored` no `.wslconfig`, o WSL passa a usar a mesma
# placa de rede do Windows — e passa a ser filtrado pelo **firewall do
# Hyper-V**, que é um conjunto de regras separado do firewall comum do Windows.
#
# A política padrão de entrada dele é `Block`. Ou seja: com a rede espelhada
# ligada e nada mais feito, a API responde perfeitamente dentro do WSL, responde
# no `localhost` do Windows, e **não responde para mais ninguém** — nem para o
# próprio Windows pelo IP da rede, nem para o celular.
#
# O sintoma é cruel porque parece problema de aplicação: o `curl` da sua máquina
# funciona, o container está `healthy`, e só o celular diz que não alcança.
#
# Criar uma regra com `New-NetFirewallRule` (o cmdlet comum, que aparece na
# maioria das respostas de internet) não muda nada aqui: ela vai para o firewall
# do Windows, e quem está bloqueando é o do Hyper-V. O cmdlet certo é
# `New-NetFirewallHyperVRule`, com o `VMCreatorId` do WSL.
# ------------------------------------------------------------------------
param(
    [switch]$Remover
)

$ErrorActionPreference = 'Stop'

# Identificador fixo do WSL no firewall do Hyper-V. Confirme com
# `Get-NetFirewallHyperVVMCreator` se um dia deixar de bater.
$WslId = '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}'

$Portas = @(
    @{ Nome = 'Atlas-API';       Porta = 8000; Descricao = 'API de telemetria — o app lê por aqui' },
    @{ Nome = 'Atlas-Mosquitto'; Porta = 1883; Descricao = 'Broker MQTT — para apontar o Raspberry Pi para esta máquina' }
)

function Ehadmin {
    $identidade = [Security.Principal.WindowsIdentity]::GetCurrent()
    (New-Object Security.Principal.WindowsPrincipal $identidade).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Ehadmin)) {
    Write-Host ''
    Write-Host '  Este script precisa de PowerShell como Administrador.' -ForegroundColor Red
    Write-Host '  Menu Iniciar -> digite "PowerShell" -> Executar como administrador' -ForegroundColor Yellow
    Write-Host ''
    exit 1
}

if ($Remover) {
    Write-Host ''
    Write-Host '==> Removendo as regras' -ForegroundColor Cyan
    foreach ($item in $Portas) {
        try {
            Remove-NetFirewallHyperVRule -Name $item.Nome -ErrorAction Stop
            Write-Host "    removida: $($item.Nome) (porta $($item.Porta))"
        } catch {
            Write-Host "    não existia: $($item.Nome)" -ForegroundColor DarkGray
        }
    }
    Write-Host ''
    exit 0
}

Write-Host ''
Write-Host '==> Estado atual do firewall do Hyper-V' -ForegroundColor Cyan
$politica = (Get-NetFirewallHyperVVMSetting -PolicyStore ActiveStore -Name $WslId).DefaultInboundAction
Write-Host "    política de entrada padrão: $politica"
if ($politica -ne 'Block') {
    Write-Host '    (já está permitindo tudo; as regras abaixo não fazem mal, mas talvez nem sejam necessárias)' -ForegroundColor DarkGray
}

Write-Host ''
Write-Host '==> Liberando as portas do ambiente de testes' -ForegroundColor Cyan
foreach ($item in $Portas) {
    # Idempotente: rodar duas vezes não duplica a regra nem falha.
    Remove-NetFirewallHyperVRule -Name $item.Nome -ErrorAction SilentlyContinue

    New-NetFirewallHyperVRule `
        -Name $item.Nome `
        -DisplayName "$($item.Nome) (WSL)" `
        -VMCreatorId $WslId `
        -Direction Inbound `
        -Protocol TCP `
        -LocalPorts $item.Porta `
        -Action Allow | Out-Null

    Write-Host "    [ ok ] $($item.Porta)  $($item.Descricao)"
}

Write-Host ''
Write-Host '==> Conferindo' -ForegroundColor Cyan
$ip = (Get-NetIPAddress -AddressFamily IPv4 |
       Where-Object { $_.IPAddress -like '192.168.*' -or $_.IPAddress -like '10.*' } |
       Select-Object -First 1 -ExpandProperty IPAddress)

if ($ip) {
    try {
        $resposta = Invoke-WebRequest -Uri "http://${ip}:8000/saude" -TimeoutSec 8 -UseBasicParsing
        Write-Host "    [ ok ] http://${ip}:8000/saude respondeu $($resposta.StatusCode)" -ForegroundColor Green
        Write-Host ''
        Write-Host '  Pronto. No app, em "Dados do robô" -> engrenagem:' -ForegroundColor Green
        Write-Host "      Endereço:  http://${ip}:8000"
        Write-Host '      Token:     atlas-token-de-teste-local'
        Write-Host ''
        Write-Host '  O celular precisa estar no mesmo Wi-Fi que esta máquina.'
    } catch {
        Write-Host "    [falha] http://${ip}:8000/saude não respondeu." -ForegroundColor Red
        Write-Host '    As regras foram criadas; o que falta é o ambiente estar de pé.' -ForegroundColor Yellow
        Write-Host '    No WSL:  ./cloud/scripts/ambiente-local.sh' -ForegroundColor Yellow
    }
} else {
    Write-Host '    não achei um IP de rede local nesta máquina' -ForegroundColor Yellow
}
Write-Host ''
