# API de leitura da telemetria. Só lê: quem grava é o ingestor.
FROM python:3.12-slim

# Sem .pyc no container (a imagem é descartável) e log sem buffer, senão o
# `docker compose logs -f` só mostra o que aconteceu quando o buffer enche.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# As dependências vêm antes do código de propósito: o Docker guarda esta
# camada, e mexer numa rota deixa de reinstalar o FastAPI inteiro.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Usuário sem privilégio: um processo exposto à internet não tem motivo para
# ser root dentro do container.
RUN useradd --system --uid 1001 api && chown -R api /app
USER api

EXPOSE 8000

# Um worker só. As consultas duram milissegundos e o pool do psycopg já
# atende várias ao mesmo tempo; mais workers multiplicariam as conexões ao
# banco sem responder mais rápido.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
