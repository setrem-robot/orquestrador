# A nuvem da Atlas na sua máquina

Sobe o broker, o banco, o ingestor e a API na sua própria máquina, com os
mesmos arquivos que vão para a VM do LARCC. A ideia é simples: **o que
funcionar aqui funciona lá**, porque não são duas descrições do sistema — é a
mesma, com um arquivo de ajustes por cima.

```
docker-compose.yml       a definição dos serviços — vale aqui E na VM
compose.local.yml        só o que muda numa máquina de mesa
.env                     as senhas — é o que diferencia um ambiente do outro
```

Migrar para a VM é **parar de passar o `compose.local.yml`** e trocar o `.env`.
Nada mais.

---

## 1. Docker, sem Docker Desktop

O WSL2 já é uma máquina Linux; o Docker Engine roda dentro dela como rodaria na
VM. Não é preciso instalar o Docker Desktop — que é uma aplicação de janela,
ocupa memória o tempo todo e tem licença paga para empresas acima de certo
porte.

```bash
sudo ./cloud/scripts/instalar-docker-wsl.sh
```

No fim ele pede um `wsl --shutdown` no PowerShell. Isso não é frescura: o grupo
de um processo é decidido quando ele nasce, então o terminal que está aberto
agora continuaria sem enxergar o Docker.

Confira com:

```bash
docker run --rm hello-world
```

> **Acabou de instalar e não quer reiniciar agora?** O script detecta que a
> sessão ainda não entrou no grupo `docker` e segue com `sudo`, avisando. O
> `wsl --shutdown` continua sendo o certo — só não precisa ser antes do
> primeiro teste.

## 2. Subir

```bash
./cloud/scripts/ambiente-local.sh
```

Na primeira vez ele baixa as imagens e compila a API — alguns minutos. Depois
sobe em segundos. O script cria o `.env` sozinho a partir de
`.env.local.example`, que já vem preenchido: **nenhum campo precisa ser
inventado antes da primeira subida.**

Ao terminar, ele confere cada peça e imprime o endereço e o token para você pôr
no app.

| Comando | O que faz |
|---|---|
| `ambiente-local.sh` | sobe tudo e confere |
| `ambiente-local.sh --robo` | sobe e deixa um robô falso publicando ao vivo |
| `ambiente-local.sh --logs` | acompanha o que cada peça está fazendo |
| `ambiente-local.sh --parar` | desliga (os dados ficam) |
| `ambiente-local.sh --zerar` | desliga e apaga o banco |
| `ambiente-local.sh --endereco` | só mostra o que pôr no app |

## 3. O celular precisa alcançar a sua máquina

Este é o único passo que costuma dar trabalho, e a causa é o WSL2.

Por padrão o WSL roda atrás de uma NAT própria. Os containers escutam num IP
interno — `172.24.x.x` — que **só existe dentro do WSL**. Do celular, esse
endereço não leva a lugar nenhum, e o sintoma é o app dizendo "não consegui
alcançar a API" com tudo funcionando perfeitamente no `curl` da sua máquina.

A solução é ligar o modo de rede espelhada, que faz o WSL usar a mesma placa de
rede do Windows. Crie `C:\Users\SEU_USUARIO\.wslconfig`:

```ini
[wsl2]
networkingMode=mirrored
firewall=true
```

E, no PowerShell:

```powershell
wsl --shutdown
```

Abra o terminal de novo e rode `./cloud/scripts/ambiente-local.sh --endereco`.
Agora o endereço mostrado é o IP da sua máquina na rede.

> Exige Windows 11 22H2 ou mais novo.

**Falta ainda o firewall** — a rede espelhada sozinha não deixa o celular
entrar. É um comando, e roda daqui mesmo:

```bash
./cloud/scripts/ambiente-local.sh --rede
```

Veja *O firewall*, logo abaixo, para o porquê.

### Se a rede espelhada não for possível

Não faça nada de diferente: rode o mesmo `--rede` da seção seguinte. Ele
detecta que o WSL está no modo NAT e, em vez da regra de firewall, cria um
**encaminhamento de porta** — o Windows escuta na 8000 e repassa para o IP
interno do WSL.

Funciona bem, com um porém que morde: **o IP do WSL muda a cada reinício**, e o
encaminhamento passa a apontar para o vazio. Quando o celular parar de alcançar
depois de reiniciar a máquina, rode o `--rede` de novo — ele reaponta. É por
isso que a rede espelhada continua sendo a recomendação: lá não há para onde
reapontar.

### O firewall — o passo que quase todo mundo erra

Ligar a rede espelhada **não basta**. Com ela, o WSL passa a ser filtrado pelo
**firewall do Hyper-V**, um conjunto de regras separado do firewall comum do
Windows, cuja política de entrada padrão é `Block`.

O sintoma não parece firewall: a API responde dentro do WSL, responde no
`127.0.0.1` do Windows, o container está `healthy` — e o celular não alcança.

**Resolve daqui mesmo, do Linux:**

```bash
./cloud/scripts/ambiente-local.sh --rede
```

Vai abrir o *"Deseja permitir que este aplicativo faça alterações?"* do Windows
— clique em **Sim**. É a única parte que não dá para fazer do Linux: mexer em
firewall exige privilégio de administrador do Windows, e nem o `sudo` o
concede.

O script descobre em qual modo de rede você está e faz o certo para cada um:

| Modo | O que ele faz |
|---|---|
| espelhado | regra no firewall do Hyper-V (`New-NetFirewallHyperVRule`) |
| NAT | encaminhamento de porta (`netsh portproxy`) + regra no firewall comum |

Escolher errado aí é passar meia hora procurando problema na API. E é fácil
errar: `New-NetFirewallRule` — o cmdlet que aparece em quase toda resposta de
internet sobre isso — **não funciona no modo espelhado**, porque cria a regra
no firewall do Windows e quem bloqueia é o do Hyper-V.

Outros usos:

```bash
./cloud/scripts/ambiente-local.sh --rede --conferir   # só diz como está
./cloud/scripts/liberar-rede.sh --remover             # desfaz
```

> **Não tente confirmar pelo Windows.** No modo espelhado, o próprio Windows
> não alcança o WSL pelo IP da rede — nem pelo da LAN, nem por um do Tailscale;
> só por `127.0.0.1`. Isso é do espelhamento, não do firewall, e foi medido
> nesta máquina com as regras já criadas e ativas. Um `Invoke-WebRequest` dali
> dá timeout e faz parecer que nada mudou.
>
> **Quem responde de verdade é o celular.** Abra o app, ponha o endereço e o
> token, e toque em *Salvar e testar* — é o único teste que vale.

### Testar a landing page junto

```bash
cd site && python3 -m http.server 8080
```

Abra `http://localhost:8080`. Servida de um endereço local, a página aponta
sozinha para a API do ambiente de testes — não é preciso editar o `config.js`
(e, portanto, não há o que lembrar de desfazer antes de publicar).

## 4. Dados para ver

Duas ferramentas, com propósitos diferentes:

**`semear-demonstracao.py`** grava direto no banco. É o atalho: enche seis
horas de histórico num instante, e prova que a API sabe ler.

```bash
python3 cloud/scripts/semear-demonstracao.py --horas 6
python3 cloud/scripts/semear-demonstracao.py --limpar
```

**`robo-falso.py`** publica no MQTT, como o robô publicaria. É o caminho
inteiro:

```
robo-falso ──► mosquitto ──► ingestor ──► TimescaleDB ──► API ──► app
```

```bash
pip install paho-mqtt
python3 cloud/scripts/robo-falso.py
```

Um ponto que aparece no mapa do celular vindo daí provou todas essas peças de
uma vez. **É o teste que vale a pena rodar antes de levar qualquer coisa para a
VM** — é ele que pega os erros de encanamento que o outro não pega: senha do
broker errada, ingestor sem assinar o tópico, `ts` num formato que o banco
recusa.

Tudo que os dois geram sai marcado com `"demo": true`, e `--limpar` apaga por
essa marca — nunca por janela de tempo, que levaria junto a telemetria de
verdade do mesmo período.

## 5. Apontar o Raspberry Pi para cá

Dá para testar o caminho de verdade, com o robô publicando na sua máquina em
vez de na VM. No Pi, em `/etc/mosquitto/conf.d/bridge.conf`:

```
address 192.168.1.102:1883
```

(sem `remote_username`/`remote_password` — o broker local aceita anônimo)

E `sudo systemctl restart mosquitto`. A telemetria do robô real passa a cair no
seu banco de testes. Para voltar, aponte de novo para o endereço do LARCC.

---

## 6. Levar para a VM do LARCC

O trabalho já está feito: o que muda são **duas coisas**.

### O que muda

| | Aqui | Na VM |
|---|---|---|
| Comando | `-f docker-compose.yml -f compose.local.yml` | `-f docker-compose.yml` |
| Broker | anônimo (`mosquitto.local.conf`) | autenticado (`mosquitto.conf` + `passwd`) |
| Portas | API e broker na rede local | API só no loopback; quem publica é o túnel |
| Senhas | as de `.env.local.example`, públicas | geradas, e só na VM |
| Túnel | não existe | `--profile tunel` |

### O passo a passo

```bash
# 1. Na VM, com o repositório clonado:
cd cloud
cp .env.example .env          # o da VM, com os campos em branco

# 2. Gere as senhas de verdade. As de teste estão num arquivo versionado,
#    no GitHub — não são segredo de ninguém.
openssl rand -hex 32          # API_TOKEN
openssl rand -hex 16          # PGPASSWORD
openssl rand -hex 16          # API_PGPASSWORD
./scripts/gen-mosquitto-passwd.sh piev 'a-senha-do-broker'

# 3. Suba SEM o compose.local.yml. É esta ausência que muda o ambiente.
docker compose up -d --build
curl http://127.0.0.1:8000/saude

# 4. O túnel, com o token criado no painel da Cloudflare:
docker compose --profile tunel up -d
curl https://api.seudominio.com.br/saude

# 5. E o CORS da landing page, que aqui era `*`:
#    CORS_ORIGENS=https://atlas.seudominio.com.br
docker compose up -d api
```

### O que **não** levar

- **`.env`** — o seu tem as senhas de teste. Na VM, comece do `.env.example`.
- **`mosquitto.local.conf`** — lá o broker recebe conexão de fora, e sem senha
  qualquer um publicaria telemetria falsa no histórico do robô.
- **Os dados de demonstração.** Antes de mostrar para alguém:
  `python3 cloud/scripts/semear-demonstracao.py --limpar`.

### Conferir que migrou de verdade

```bash
docker compose ps --format '{{.Service}}\t{{.State}}'
curl -s http://127.0.0.1:8000/saude | grep -o '"publico":[a-z]*'
docker compose exec mosquitto mosquitto_sub -t 'robo/telemetria/#' -C 1 -W 5
```

O último comando deve **falhar** por autenticação na VM — se ele funcionar sem
usuário e senha, o broker subiu com a configuração local, e é isso que você
precisa saber antes de alguém descobrir por conta.
