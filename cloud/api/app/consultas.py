"""As perguntas que o app faz ao histórico, como SQL.

Cada função devolve `(sql, parametros)` e **não toca no banco**. É o mesmo
desenho de `roteador.py` e de `motores/cinematica.py`, pelo mesmo motivo: o que
decide fica testável sem infraestrutura. Um teste aqui confere que o limite foi
saturado e que a janela de tempo virou o intervalo certo, sem precisar de um
TimescaleDB de pé.

Nenhuma consulta escreve. A API é só de leitura — quem grava é o `ingestor`.

## Por que os parâmetros são saturados, e não recusados

O app é cliente não-confiável tanto quanto o celular que manda comandos de
motor. Um `limite=99999999` numa hypertable com meses de histórico é uma
consulta que trava a VM inteira, e o robô não tem quem esteja de plantão para
matá-la. Saturar no teto responde uma coisa útil em vez de um erro — e o teto
está escrito aqui, não espalhado pelas rotas.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

#: Tetos de tudo que o app pode pedir. Existem para uma pergunta mal feita não
#: virar uma consulta que ninguém consegue interromper.
MAX_PONTOS = 5000
MAX_EVENTOS = 500
MAX_JANELA = timedelta(days=90)

#: Janela usada quando o app não pede nenhuma. Um dia é o que cabe na tela do
#: celular sem virar uma mancha.
JANELA_PADRAO = timedelta(days=1)

#: Intervalos aceitos na agregação por tempo. É lista fechada de propósito:
#: `time_bucket` recebe um intervalo, e montar esse texto a partir do que o
#: cliente digitou seria injeção de SQL com outro nome.
INTERVALOS: dict[str, str] = {
    "1m": "1 minute",
    "5m": "5 minutes",
    "15m": "15 minutes",
    "1h": "1 hour",
    "6h": "6 hours",
    "1d": "1 day",
}

#: Tipos de telemetria que o robô produz hoje (o último segmento de
#: `robo/telemetria/<tipo>`). Ver `docs/contrato-mqtt.md`.
TIPOS_CONHECIDOS = ("gps", "motores", "bateria", "wifi")


def agora() -> datetime:
    return datetime.now(tz=timezone.utc)


def janela(
    desde: datetime | None, ate: datetime | None
) -> tuple[datetime, datetime]:
    """Resolve o par (início, fim) de uma consulta, com limites.

    Faltando os dois, vale a última `JANELA_PADRAO`. Uma janela invertida — o
    app mandou o fim antes do início — é endireitada em vez de recusada: o
    resultado é o que a pessoa queria ver de qualquer jeito.
    """
    fim = ate or agora()
    inicio = desde or (fim - JANELA_PADRAO)
    if inicio > fim:
        inicio, fim = fim, inicio
    if fim - inicio > MAX_JANELA:
        inicio = fim - MAX_JANELA
    return inicio, fim


def limitar(valor: int | None, teto: int, padrao: int) -> int:
    if valor is None:
        return padrao
    return max(1, min(teto, valor))


# ---------------------------------------------------------------------------
# Estado atual
# ---------------------------------------------------------------------------
def estado_atual() -> tuple[str, tuple]:
    """A última mensagem de cada tipo, numa consulta só.

    `DISTINCT ON` é do Postgres e resolve isto sem subconsulta por tipo: ordena
    por (tipo, ts desc) e fica com a primeira linha de cada tipo. Numa
    hypertable com índice em (tipo, ts DESC) — que o schema já cria — é uma
    varredura de índice por tipo, e não uma leitura da tabela inteira.
    """
    sql = """
        SELECT DISTINCT ON (tipo) tipo, ts, payload
        FROM telemetria
        WHERE ts > now() - INTERVAL '7 days'
        ORDER BY tipo, ts DESC
    """
    return sql, ()


# ---------------------------------------------------------------------------
# Trajeto do GPS
# ---------------------------------------------------------------------------
def trajeto(
    desde: datetime | None, ate: datetime | None, limite: int | None
) -> tuple[str, tuple]:
    """Os pontos do percurso, do mais antigo para o mais novo.

    Só entram pontos com `fix` verdadeiro: um GPS sem sinal publica lat/lon
    zerados ou a última posição conhecida, e desenhar isso no mapa põe o robô
    no golfo da Guiné — que é onde fica a coordenada (0, 0).

    A ordenação final é crescente porque o mapa desenha uma linha, e uma linha
    ligada de trás para frente é a mesma linha; mas o `LIMIT` precisa pegar os
    pontos **mais recentes**, e para isso a ordenação de dentro é decrescente.
    """
    inicio, fim = janela(desde, ate)
    quantos = limitar(limite, MAX_PONTOS, 1000)
    sql = """
        SELECT ts, payload
        FROM (
            SELECT ts, payload
            FROM telemetria
            WHERE tipo = 'gps'
              AND ts BETWEEN %s AND %s
              AND (payload->>'fix')::boolean IS TRUE
            ORDER BY ts DESC
            LIMIT %s
        ) recentes
        ORDER BY ts ASC
    """
    return sql, (inicio, fim, quantos)


# ---------------------------------------------------------------------------
# Série histórica
# ---------------------------------------------------------------------------
def serie(
    tipo: str,
    campo: str,
    desde: datetime | None,
    ate: datetime | None,
    intervalo: str,
) -> tuple[str, tuple]:
    """Média de um campo numérico do payload, agrupada no tempo.

    `time_bucket` é a função do TimescaleDB que faz isto ser barato: ela agrupa
    por faixa de tempo usando a própria partição da hypertable. Sem ela, um
    gráfico de trinta dias leria linha por linha.

    O intervalo entra no texto do SQL porque `time_bucket` não aceita
    parâmetro para ele — por isso ele vem de `INTERVALOS`, uma lista fechada,
    e nunca do que o cliente escreveu. O `campo` também é interpolado, e é
    validado da mesma forma por quem chama (`campo_valido`).
    """
    inicio, fim = janela(desde, ate)
    passo = INTERVALOS[intervalo]
    sql = f"""
        SELECT time_bucket('{passo}', ts) AS instante,
               avg((payload->>'{campo}')::double precision) AS valor,
               count(*) AS amostras
        FROM telemetria
        WHERE tipo = %s
          AND ts BETWEEN %s AND %s
          AND payload ? '{campo}'
        GROUP BY instante
        ORDER BY instante ASC
    """
    return sql, (tipo, inicio, fim)


def campo_valido(campo: str) -> bool:
    """Se o nome do campo pode ser interpolado no SQL com segurança.

    Letras, dígitos e sublinhado apenas — é o formato de toda chave que o robô
    publica (`percentual`, `velocidade_kmh`, `satelites`). Qualquer coisa fora
    disso é recusada antes de chegar perto da consulta.
    """
    return bool(campo) and len(campo) <= 40 and campo.replace("_", "").isalnum()


# ---------------------------------------------------------------------------
# Eventos crus
# ---------------------------------------------------------------------------
def eventos(
    tipo: str | None, limite: int | None, antes_de: datetime | None
) -> tuple[str, tuple]:
    """As últimas mensagens como chegaram, do mais novo para o mais antigo.

    `antes_de` é a paginação: a tela pede a próxima página passando o `ts` da
    última linha que já tem. Paginar por instante, e não por deslocamento,
    evita a lista pular ou repetir linhas quando telemetria nova chega no meio
    da rolagem — e num robô ligado ela chega o tempo todo.
    """
    quantos = limitar(limite, MAX_EVENTOS, 100)
    condicoes = ["ts > now() - INTERVAL '90 days'"]
    parametros: list = []

    if tipo:
        condicoes.append("tipo = %s")
        parametros.append(tipo)
    if antes_de is not None:
        condicoes.append("ts < %s")
        parametros.append(antes_de)

    parametros.append(quantos)
    sql = f"""
        SELECT ts, tipo, topico, payload
        FROM telemetria
        WHERE {" AND ".join(condicoes)}
        ORDER BY ts DESC
        LIMIT %s
    """
    return sql, tuple(parametros)


def resumo() -> tuple[str, tuple]:
    """Quantas mensagens de cada tipo, e a mais recente de cada.

    É o que a landing page mostra sem precisar de nenhum dado sensível: dá para
    ver que o robô existe e está vivo sem expor onde ele está.
    """
    sql = """
        SELECT tipo, count(*) AS total, max(ts) AS ultima
        FROM telemetria
        GROUP BY tipo
        ORDER BY tipo
    """
    return sql, ()
