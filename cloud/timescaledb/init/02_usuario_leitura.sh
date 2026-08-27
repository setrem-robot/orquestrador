#!/bin/bash
#
# Cria o usuário que a API usa: SELECT e nada mais.
#
# Roda automaticamente na PRIMEIRA subida do banco, junto com 01_schema.sql.
# Num banco que já existe ele não roda — aplique à mão, uma vez:
#
#   docker exec -it timescaledb psql -U robo -d robo
#   CREATE ROLE robo_leitura LOGIN PASSWORD 'a-senha-do-.env';
#   GRANT CONNECT ON DATABASE robo TO robo_leitura;
#   GRANT USAGE ON SCHEMA public TO robo_leitura;
#   GRANT SELECT ON ALL TABLES IN SCHEMA public TO robo_leitura;
#   ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO robo_leitura;
#
# POR QUE UM USUÁRIO SEPARADO
#
# A API é a única peça do sistema exposta à internet. Se ela usasse o mesmo
# usuário do ingestor, qualquer falha numa rota — uma injeção que passasse pela
# validação, um bug numa consulta — teria permissão para apagar meses de
# telemetria. Com este usuário, o pior caso é ler o que já era para ser lido.
#
# É a mesma ideia do `vigia.py` nos motores: a pergunta não é "isto vai dar
# errado?", é "o que acontece quando der?".
set -euo pipefail

if [[ -z "${API_PGUSER:-}" || -z "${API_PGPASSWORD:-}" ]]; then
    echo "[init] API_PGUSER/API_PGPASSWORD não definidos; pulando o usuário de leitura." >&2
    echo "[init] A API não vai conseguir conectar até ele existir." >&2
    exit 0
fi

psql -v ON_ERROR_STOP=1 --username "${POSTGRES_USER}" --dbname "${POSTGRES_DB}" <<-SQL
    CREATE ROLE ${API_PGUSER} LOGIN PASSWORD '${API_PGPASSWORD}';

    GRANT CONNECT ON DATABASE ${POSTGRES_DB} TO ${API_PGUSER};
    GRANT USAGE ON SCHEMA public TO ${API_PGUSER};
    GRANT SELECT ON ALL TABLES IN SCHEMA public TO ${API_PGUSER};

    -- Sem isto, uma tabela criada depois nasceria invisível para a API — e o
    -- sintoma seria uma rota nova respondendo "permission denied" sem que
    -- ninguém tivesse mexido em permissão nenhuma.
    ALTER DEFAULT PRIVILEGES IN SCHEMA public
        GRANT SELECT ON TABLES TO ${API_PGUSER};
SQL

echo "[init] usuário de leitura ${API_PGUSER} criado."
