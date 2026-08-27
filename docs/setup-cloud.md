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
