"""API de leitura da telemetria do robô.

O que faltava para fechar o círculo. A telemetria já ia do robô até o
TimescaleDB — mas o app não tinha como voltar a lê-la: um celular não fala
Postgres, e abrir o banco para a internet para que ele falasse seria trocar um
buraco por um bem maior. Esta API é a peça do meio, e é **só de leitura**:
quem grava continua sendo o `ingestor`.

    robô ──MQTT──► bridge ──► broker remoto ──► ingestor ──► TimescaleDB
                                                                 │
                                              app ◄── esta API ◄─┘
                                     landing page ◄──┘

Duas portas, com propósitos diferentes: `/v1/...` exige token e serve tudo;
`/v1/publico/...` não exige nada e serve menos (ver `seguranca.py`).

Configuração por variáveis de ambiente:
    API_TOKEN               o que o app manda em `Authorization: Bearer`
    CORS_ORIGENS            origens da landing page, separadas por vírgula
    PUBLICO_HABILITADO      (default true) liga as rotas sem token
    PRECISAO_GPS_PUBLICA    (default 4) casas decimais nas rotas sem token
    PGHOST/PGPORT/PGUSER/PGPASSWORD/PGDATABASE
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import consultas, seguranca
from .banco import Banco

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("api")

banco = Banco()

#: Teto por IP. Generoso para o app (que atualiza a cada poucos segundos) e
#: apertado o bastante para uma aba esquecida não derrubar a VM.
limitador = seguranca.Limitador(teto=120, janela_s=60.0)


@asynccontextmanager
async def ciclo_de_vida(_: FastAPI):
    banco.abrir()
    if not seguranca.token_configurado():
        # Aviso gritado no arranque: sem token, as rotas do app recusam todo
        # mundo, e o sintoma (401 em tudo) não diz onde está o problema.
        logger.error("API_TOKEN vazio: as rotas do app vão recusar todas as requisições")
    yield
    banco.fechar()


app = FastAPI(
    title="Atlas — telemetria",
    description="Leitura do histórico do robô Atlas (Engenharia de Computação, Setrem).",
    version="1.0.0",
    lifespan=ciclo_de_vida,
)


#: Cabeçalhos de segurança em toda resposta. São baratos e cobrem o caso em que
#: alguém aponta um navegador — ou uma página de terceiros — para esta API, que
#: hoje serve só JSON mas responde a qualquer origem que a alcance pelo túnel.
#:
#: - **nosniff**: o navegador não tenta adivinhar que o JSON é HTML e executá-lo.
#: - **frame-ancestors / X-Frame-Options**: a resposta não pode ser embutida num
#:   iframe de outro site (clickjacking não se aplica a JSON, mas custa uma linha).
#: - **CSP `default-src 'none'`**: uma resposta desta API não carrega script,
#:   imagem nem estilo de lugar nenhum — não há o que carregar, e dizer isso
#:   fecha a porta a um XSS refletido caso uma rota um dia devolva HTML por engano.
#: - **HSTS**: o navegador passa a exigir HTTPS neste host pelos próximos dois
#:   anos. Só afeta quem chega pelo domínio da Cloudflare (o único caminho até
#:   aqui), e nunca `includeSubDomains`, para não impor a regra ao resto do
#:   domínio pessoal de quem hospeda.
_CABECALHOS_SEGURANCA = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "Strict-Transport-Security": "max-age=63072000",
}


@app.middleware("http")
async def cabecalhos_de_seguranca(request: Request, call_next):
    resposta = await call_next(request)
    for chave, valor in _CABECALHOS_SEGURANCA.items():
        resposta.headers.setdefault(chave, valor)
    return resposta

_origens = [o.strip() for o in os.environ.get("CORS_ORIGENS", "").split(",") if o.strip()]
if _origens:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origens,
        # Só leitura: nenhuma rota daqui muda nada, então nem GET precisa de
        # credencial de navegador. Manter fechado evita que uma página de
        # terceiros use o cookie de alguém para falar com a API.
        allow_credentials=False,
        allow_methods=["GET"],
        allow_headers=["Authorization"],
    )


# ---------------------------------------------------------------------------
# Porteiros
# ---------------------------------------------------------------------------
def _cliente(request: Request) -> str:
    """Quem está pedindo, para o limitador.

    Atrás do túnel da Cloudflare o IP da conexão é sempre o do `cloudflared`,
    e limitar por ele limitaria todo mundo junto. `CF-Connecting-IP` é o
    cabeçalho que a Cloudflare põe com o IP real — e só dá para confiar nele
    porque nada além do túnel alcança esta API.
    """
    real = request.headers.get("CF-Connecting-IP") or request.headers.get(
        "X-Forwarded-For", ""
    )
    if real:
        return real.split(",")[0].strip()
    return request.client.host if request.client else "desconhecido"


async def exige_token(
    request: Request, authorization: str | None = Header(default=None)
) -> None:
    if not limitador.permitir(_cliente(request)):
        raise HTTPException(status_code=429, detail="requisições demais; tente em um minuto")
    if not seguranca.token_confere(authorization):
        raise HTTPException(status_code=401, detail="token ausente ou inválido")


async def porta_publica(request: Request) -> None:
    if not seguranca.PUBLICO_HABILITADO:
        raise HTTPException(status_code=404, detail="rotas públicas desligadas")
    if not limitador.permitir(_cliente(request)):
        raise HTTPException(status_code=429, detail="requisições demais; tente em um minuto")


# ---------------------------------------------------------------------------
# Saúde
# ---------------------------------------------------------------------------
@app.get("/saude", tags=["saúde"])
async def saude() -> JSONResponse:
    """Sem token de propósito: é o healthcheck do Docker e do túnel.

    Não conta nada sobre o robô — só se esta API consegue falar com o banco.
    """
    de_pe, erro = banco.esta_de_pe()
    corpo: dict[str, Any] = {
        "ok": de_pe,
        "banco": de_pe,
        "token_configurado": seguranca.token_configurado(),
        "publico": seguranca.PUBLICO_HABILITADO,
    }
    if erro:
        corpo["erro"] = erro
    return JSONResponse(corpo, status_code=200 if de_pe else 503)


# ---------------------------------------------------------------------------
# Rotas do app
# ---------------------------------------------------------------------------
@app.get("/v1/estado", tags=["app"], dependencies=[Depends(exige_token)])
async def estado() -> dict:
    """O último valor de cada tipo de telemetria — o painel do app.

    Vem com `idade_s` já calculado. Sem ele, a tela mostraria "bateria 83%" com
    a mesma cara para um dado de agora e para um de anteontem, que é
    exatamente o erro que faz alguém confiar num robô desligado.
    """
    linhas = banco.consultar(*consultas.estado_atual())
    agora = consultas.agora()
    itens = {
        tipo: {
            "ts": _iso(ts),
            "idade_s": round((agora - ts).total_seconds(), 1),
            "dados": payload,
        }
        for tipo, ts, payload in linhas
    }
    return {"gerado_em": _iso(agora), "itens": itens}


@app.get("/v1/trajeto", tags=["app"], dependencies=[Depends(exige_token)])
async def trajeto(
    desde: datetime | None = None,
    ate: datetime | None = None,
    limite: int | None = Query(default=None, ge=1),
) -> dict:
    """Os pontos do percurso, para desenhar a linha no mapa."""
    linhas = banco.consultar(*consultas.trajeto(desde, ate, limite))
    pontos = [_ponto(ts, payload) for ts, payload in linhas]
    return {"pontos": pontos, "total": len(pontos)}


@app.get("/v1/serie/{tipo}", tags=["app"], dependencies=[Depends(exige_token)])
async def serie(
    tipo: str,
    campo: str = Query(description="campo numérico do payload, ex.: percentual"),
    intervalo: str = Query(default="1h"),
    desde: datetime | None = None,
    ate: datetime | None = None,
) -> dict:
    """Média de um campo ao longo do tempo — o gráfico do app."""
    if intervalo not in consultas.INTERVALOS:
        opcoes = ", ".join(consultas.INTERVALOS)
        raise HTTPException(status_code=400, detail=f"intervalo inválido (use: {opcoes})")
    if not consultas.campo_valido(campo):
        raise HTTPException(status_code=400, detail="nome de campo inválido")

    linhas = banco.consultar(*consultas.serie(tipo, campo, desde, ate, intervalo))
    return {
        "tipo": tipo,
        "campo": campo,
        "intervalo": intervalo,
        "pontos": [
            {"ts": _iso(instante), "valor": valor, "amostras": amostras}
            for instante, valor, amostras in linhas
            if valor is not None
        ],
    }


@app.get("/v1/eventos", tags=["app"], dependencies=[Depends(exige_token)])
async def eventos(
    tipo: str | None = None,
    limite: int | None = Query(default=None, ge=1),
    antes_de: datetime | None = Query(default=None, description="paginação: ts da última linha"),
) -> dict:
    """As mensagens como chegaram. Feio, e é o que salva uma depuração em campo."""
    linhas = banco.consultar(*consultas.eventos(tipo, limite, antes_de))
    registros = [
        {"ts": _iso(ts), "tipo": t, "topico": topico, "dados": payload}
        for ts, t, topico, payload in linhas
    ]
    return {
        "eventos": registros,
        # A próxima página começa antes da última linha desta. Vazio quando
        # acabou — é assim que a tela sabe parar de pedir.
        "proximo_antes_de": registros[-1]["ts"] if len(registros) >= 1 else None,
    }


# ---------------------------------------------------------------------------
# Rotas da landing page (sem token, e servindo menos)
# ---------------------------------------------------------------------------
@app.get("/v1/publico/resumo", tags=["público"], dependencies=[Depends(porta_publica)])
async def resumo_publico() -> dict:
    """Quantas mensagens de cada tipo, e quando foi a última.

    Mostra que o robô existe e está vivo sem contar onde ele está.
    """
    linhas = banco.consultar(*consultas.resumo())
    agora = consultas.agora()
    return {
        "gerado_em": _iso(agora),
        "tipos": [
            {
                "tipo": tipo,
                "total": total,
                "ultima": _iso(ultima),
                "idade_s": round((agora - ultima).total_seconds(), 1) if ultima else None,
            }
            for tipo, total, ultima in linhas
        ],
    }


@app.get("/v1/publico/trajeto", tags=["público"], dependencies=[Depends(porta_publica)])
async def trajeto_publico(limite: int | None = Query(default=200, ge=1, le=500)) -> dict:
    """O percurso recente, com a precisão reduzida.

    ~11 metros de resolução: dá para ver o robô andando pelo campus, e não dá
    para saber em que sala ele está. Ver `PRECISAO_GPS_PUBLICA`.
    """
    linhas = banco.consultar(*consultas.trajeto(None, None, limite))
    pontos = []
    for ts, payload in linhas:
        ponto = _ponto(ts, payload)
        ponto["lat"] = seguranca.arredondar_coordenada(ponto["lat"])
        ponto["lon"] = seguranca.arredondar_coordenada(ponto["lon"])
        # Contagem de satélites e velocidade não dizem onde o robô está, mas
        # também não interessam a quem só está vendo a página: fora.
        ponto.pop("satelites", None)
        pontos.append(ponto)
    return {"pontos": pontos, "total": len(pontos), "precisao_casas": seguranca.PRECISAO_GPS_PUBLICA}


# ---------------------------------------------------------------------------
def _iso(momento: datetime | None) -> str | None:
    """Instante em ISO 8601 com fuso — o formato que `DateTime.parse` do Dart lê."""
    return momento.isoformat() if momento else None


def _ponto(ts: datetime, payload: dict) -> dict:
    """Uma leitura de GPS no formato que o mapa espera."""
    return {
        "ts": _iso(ts),
        "lat": _numero(payload.get("lat")),
        "lon": _numero(payload.get("lon")),
        "velocidade_kmh": _numero(payload.get("velocidade_kmh")),
        "satelites": payload.get("satelites"),
    }


def _numero(valor: Any) -> float | None:
    """Converte com tolerância: o payload é JSON livre e pode trazer texto."""
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None
