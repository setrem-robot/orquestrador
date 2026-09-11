# Setup do Ambiente Cloud

A nuvem roda numa VM e é composta por três containers (Docker Compose):

- **mosquitto** — broker MQTT remoto, autenticado, que recebe a telemetria
  replicada pela *bridge* do Pi.
- **timescaledb** — banco de séries temporais com o histórico de telemetria.
- **ingestor** — assina `robo/telemetria/#` no broker remoto e grava cada
  mensagem na hypertable `telemetria` do TimescaleDB.

## 1. Pré-requisitos

- Uma VM com Docker + plugin Compose.
- A porta `1883` aberta para o Pi alcançar o broker (idealmente restrita ao IP
  do robô / VPN). O TimescaleDB fica fechado (só loopback da VM).

## 2. Senhas e variáveis

### Broker MQTT
Gere o arquivo de senhas do broker remoto (cria o usuário que a bridge do Pi e
o ingestor vão usar):

```bash
./scripts/gen-mosquitto-passwd.sh piev 'umaSenhaForte'
```

O **mesmo** par usuário/senha vai no `bridge.conf` do Pi
(`remote_username` / `remote_password`).

### Variáveis do compose
Copie o exemplo e preencha as senhas:

```bash
cd cloud
cp .env.example .env
# edite .env:
#   MQTT_USERNAME / MQTT_PASSWORD  -> o mesmo par criado acima
#   PGUSER / PGPASSWORD / PGDATABASE
```

## 3. Subir tudo

```bash
cd cloud
docker compose up -d --build
docker compose ps
docker compose logs -f ingestor
```

Na primeira subida o TimescaleDB executa `timescaledb/init/01_schema.sql`, que
cria a hypertable `telemetria`. O ingestor espera o banco ficar saudável
(`depends_on` + healthcheck) antes de começar a gravar.

## 4. Verificação

Publique uma telemetria de teste no broker remoto e confira no banco:

```bash
# Publica como o robô faria (use as credenciais do .env).
docker exec -it mosquitto-remote \
  mosquitto_pub -u piev -P 'umaSenhaForte' \
  -t robo/telemetria/gps -m '{"lat":-28.2,"lon":-54.0,"fix":true,"ts":1700000000}'

# Lê de volta do TimescaleDB.
docker exec -it timescaledb \
  psql -U robo -d robo -c "SELECT ts, tipo, payload FROM telemetria ORDER BY ts DESC LIMIT 5;"
```

## Estrutura da tabela

```sql
telemetria (
    ts      TIMESTAMPTZ,   -- instante do dado (campo "ts" do payload, ou chegada)
    tipo    TEXT,          -- último segmento do tópico: gps, motores, bateria...
    topico  TEXT,          -- tópico completo de origem
    payload JSONB          -- a mensagem inteira
)  -- hypertable particionada por ts
```

Exemplos de consulta:

```sql
-- Última posição conhecida do robô.
SELECT payload FROM telemetria WHERE tipo = 'gps' ORDER BY ts DESC LIMIT 1;

-- Trajeto da última hora (lat/lon).
SELECT ts, payload->>'lat' AS lat, payload->>'lon' AS lon
FROM telemetria
WHERE tipo = 'gps' AND ts > now() - INTERVAL '1 hour'
ORDER BY ts;
```

## Variáveis de ambiente

| Variável        | Default       | Container  |
|-----------------|---------------|------------|
| `MQTT_USERNAME` | (obrigatório) | ingestor   |
| `MQTT_PASSWORD` | (obrigatório) | ingestor   |
| `MQTT_TOPIC`    | `robo/telemetria/#` | ingestor |
| `PGUSER`        | `robo`        | db/ingestor |
| `PGPASSWORD`    | (obrigatório) | db/ingestor |
| `PGDATABASE`    | `robo`        | db/ingestor |

## O robô sai de campo, e a rede cai

A telemetria não pode depender de o Wi-Fi estar de pé no instante em que o dado
acontece. O caminho inteiro foi montado para sobreviver a uma queda:

1. Os serviços publicam no broker **local**, no próprio Pi. Isso nunca falha por
   causa da internet.
2. A bridge replica `robo/telemetria/#` com **QoS 1** e `cleansession false`.
   Enquanto o LARCC estiver fora de alcance, o broker local **guarda** o que não
   subiu, em vez de descartar.
3. Quando a VM volta, tudo sobe de uma vez. O `ingestor` grava usando o campo
   `ts` do payload — e não o instante em que a mensagem chegou —, então o que
   subiu com três horas de atraso entra no banco **na hora certa**. Não há nada
   a reenviar à mão, e o gráfico não ganha um degrau falso.

O tamanho dessa fila é a única coisa que precisa de decisão, e o padrão do
Mosquitto não serve: 1000 mensagens são cerca de **dezessete minutos** de queda
no ritmo atual (~2 msg/s somando GPS, motores, bateria e Wi-Fi). Passando disso,
o Mosquitto descarta as mais antigas em silêncio — e o buraco só aparece
semanas depois, na consulta. Por isso `pi/mosquitto/config/mosquitto.conf` sobe
o limite para 100 mil mensagens (~14 horas) com teto de 32 MiB, que é o que
protege o cartão SD.

Para ver de que lado está o problema, no Pi:

```bash
./pi/scripts/verificar-telemetria.sh
```

Ele diz se a bridge está ligada, quantas mensagens estão esperando na fila e
qual foi o último valor de cada tipo.

### Um broker, não dois

O Pi pode acabar com **dois** Mosquitto instalados, e os dois querem a porta
1883: o do `pi/docker-compose.yml`, deste repositório, e o que
`scripts/setup-raspberry-pi.sh --bluetooth-app` (repositório RobotEye) instala
pelo apt para a ponte Bluetooth entregar os comandos do celular.

O segundo a subir falha com *"Address already in use"*, e o sintoma não parece
um problema de broker: o app conecta, os comandos chegam ao Pi e o robô não se
mexe — porque a ponte publica com sucesso num broker que ninguém mais escuta.

Escolha um. Num Pi que roda a face, a escuta e os motores, o do apt é o mais
simples; nesse caso a configuração que vale é
`pi/mosquitto/apt/robo.conf.example` (fila, persistência e listener), e o
`docker-compose.yml` do Pi não deve subir.

---

# A API de leitura (o app e a landing page)

Até aqui a telemetria só ia numa direção: do robô para o banco. A API é o
caminho de volta — o app e a landing page leem por ela. **Só leitura**: quem
grava continua sendo o `ingestor`.

```
robô ──MQTT──► bridge ──► broker remoto ──► ingestor ──► TimescaleDB
                                                              │
                                           app ◄── API ◄──────┘
                                  landing page ◄──┘
```

Um celular não fala Postgres, e abrir o banco para a internet para que ele
falasse seria trocar um buraco por um bem maior.

## 1. Variáveis novas no `.env`

```bash
# O token que o app manda em `Authorization: Bearer`. Gere um de verdade:
openssl rand -hex 32
```

Preencha no `cloud/.env`:

| Variável | Para que serve |
|---|---|
| `API_TOKEN` | acesso do app a **todo** o histórico, coordenadas exatas incluídas |
| `API_PGUSER` / `API_PGPASSWORD` | usuário do banco **só de leitura** que a API usa |
| `CORS_ORIGENS` | o domínio da landing page (vazio = nenhum navegador chama a API) |
| `PUBLICO_HABILITADO` | liga as rotas sem token (`true` por padrão) |
| `PRECISAO_GPS_PUBLICA` | casas decimais nas rotas públicas (`4` ≈ 11 m) |

## 2. O usuário de leitura

A API é a única peça exposta à internet. Se ela usasse o usuário do `ingestor`,
uma falha numa rota teria permissão para apagar meses de telemetria — com este
usuário, o pior caso é ler o que já era para ser lido.

Num banco **novo**, `timescaledb/init/02_usuario_leitura.sh` o cria sozinho.
Num banco **que já existe**, aplique uma vez:

```bash
docker exec -it timescaledb psql -U robo -d robo
```
```sql
CREATE ROLE robo_leitura LOGIN PASSWORD 'a-senha-do-.env';
GRANT CONNECT ON DATABASE robo TO robo_leitura;
GRANT USAGE ON SCHEMA public TO robo_leitura;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO robo_leitura;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO robo_leitura;
```

## 3. Subir

```bash
cd cloud
docker compose up -d --build api
curl http://127.0.0.1:8000/saude
```

Resposta esperada:

```json
{"ok": true, "banco": true, "token_configurado": true, "publico": true}
```

`token_configurado: false` significa que **todas** as rotas do app vão recusar
com 401 — a API não abre a porta sem token, porque servir o histórico inteiro a
quem descobrir o endereço é o modo de falhar que ninguém percebe até ser tarde.

## 4. Publicar no seu domínio (Cloudflare Tunnel)

Assim a VM **não precisa de IP público nem de porta aberta no firewall do
LARCC**: o túnel abre a conexão de dentro para fora, e a Cloudflare passa a
entregar as requisições por ela. O HTTPS válido vem junto e renovado.

**O desenho aqui é por arquivo, não por token de painel.** A rota do domínio
mora em `cloudflared/config.yml` (versionado), e o túnel entra em
`atlas.kerlonr.com.br` inteiro → **`http://caddy:80`**. Quem separa a API da
landing page é o Caddy, não o painel: ele corta o prefixo `/api` e repassa à
API (a raiz do domínio fica livre para o site). Assim a rota é revisável no
Git, e a URL do app é sempre `https://SEU_DOMINIO/api`.

As credenciais do túnel (um `.json` e o `cert.pem`) ficam em `cloudflared/`,
**fora do git**. Cria-se uma vez, na máquina que tiver um navegador:

```bash
# Autoriza a zona (abre o navegador). Numa VM headless, rode isto no seu PC
# e copie o cert.pem gerado para ~/.cloudflared/ da VM.
cloudflared tunnel login

# Cria o túnel (gera o <uuid>.json) e aponta o DNS para ele.
cloudflared tunnel create atlas-api
cloudflared tunnel route dns atlas-api atlas.kerlonr.com.br
```

Depois, ponha o `<uuid>.json`, o `cert.pem` e ajuste o `tunnel:`/
`credentials-file:` do `cloudflared/config.yml` com esse UUID. As credenciais
precisam ser legíveis pelo usuário do container (`chmod 644 cloudflared/*.json
cloudflared/cert.pem`). Então:

```bash
docker compose --profile tunel up -d
curl https://atlas.kerlonr.com.br/api/saude
```

Tudo num comando: `mosquitto`, `timescaledb`, `ingestor`, `api`, `caddy` e o
`cloudflared`. Sem o perfil `tunel`, os cinco primeiros sobem e o túnel não —
que é o modo de rodar localmente.

### Se der erro 1033 (nenhum túnel atende)

O hostname existe no DNS mas nenhum conector o serve. Confira que o
`cloudflared` subiu e registrou conexões:

```bash
docker compose logs cloudflared | grep -c 'Registered tunnel connection'   # espera 4
```

Zero: veja se ele leu as credenciais (`permission denied` no log = arquivo sem
leitura para o usuário do container; `chmod 644`). Se o DNS aponta para outro
túnel, refaça o `route dns` com `--overwrite-dns`.

### Se der 502

O `config.yml` aponta o serviço errado. Dentro da rede do compose o Caddy é
`caddy:80` (nome do serviço), nunca `localhost`. Confira o `ingress:` do
`cloudflared/config.yml` e o log do Caddy:

```bash
docker compose logs caddy | tail
```

**Mais de um conector no mesmo túnel** também reparte o tráfego e dá 502
intermitente na fatia de quem não alcança o Caddy. Rode o teste algumas vezes
e veja se todas as requisições chegam ao Caddy; se faltar, apague os conectores
extras em **Tunnels → o túnel → Connectors**.

## 5. As rotas

Todas as de `/v1/` exigem `Authorization: Bearer $API_TOKEN`.

| Rota | O que devolve |
|---|---|
| `GET /saude` | se a API fala com o banco (sem token; é o healthcheck) |
| `GET /v1/estado` | a última leitura de cada tipo, com a idade já calculada |
| `GET /v1/trajeto?desde=&ate=&limite=` | pontos do percurso, só com sinal de GPS válido |
| `GET /v1/serie/{tipo}?campo=&intervalo=` | média de um campo por faixa de tempo |
| `GET /v1/eventos?tipo=&limite=&antes_de=` | as mensagens cruas, paginadas por instante |
| `GET /v1/publico/resumo` | contagem por tipo (sem token) |
| `GET /v1/publico/trajeto` | percurso com posição arredondada (sem token) |

```bash
TOKEN=$(grep ^API_TOKEN cloud/.env | cut -d= -f2)
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/v1/estado | jq
```

`intervalo` aceita `1m`, `5m`, `15m`, `1h`, `6h`, `1d`. É lista fechada porque
ele entra no texto do `time_bucket` — montá-lo a partir do que o cliente
digitou seria injeção de SQL com outro nome.

### Duas portas, servindo coisas diferentes

A landing page é uma página estática: qualquer token no JavaScript dela é
legível por quem abrir o inspetor. Em vez de fingir que seria segredo, as rotas
`/v1/publico/` não pedem nada e **servem menos** — só o resumo e o trajeto com a
posição arredondada para ~11 metros. Dá para ver o robô andando pelo campus, e
não para saber em que sala ele está.

## 6. Ver as telas antes de o robô existir

O GPS ainda não está montado, e ninguém publica bateria. Sem dados, o app
mostra tela vazia — e tela vazia não distingue "o mapa está errado" de "não há
o que mostrar". Este script separa as duas coisas:

```bash
python3 cloud/scripts/semear-demonstracao.py --horas 6
python3 cloud/scripts/semear-demonstracao.py --limpar
```

Ele gera um trajeto em volta do campus com velocidade coerente, uma bateria que
só cai, comandos de motor que combinam com a curva, e quedas de Wi-Fi. Tudo
marcado com `"demo": true` no payload — é o que faz `--limpar` nunca tocar em
telemetria de verdade.

## 7. Testes

A parte que erra por descuido — limite não saturado, janela invertida, campo
entrando no SQL sem validação — é testável sem banco e sem container:

```bash
cd cloud/api && python3 -m unittest discover -s tests
```
