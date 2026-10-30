"""Servidor Web Assíncrono do Dashboard HUD Sci-Fi do A.N.A.K.I.N (FastAPI + SSE).

Disponibiliza rotas estáticas para os assets do framework, endpoints JSON de telemetria
e streaming em tempo real via Server-Sent Events (SSE).
"""

from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncGenerator, Optional
import argparse
import asyncio
import json
import sys

# Garante a resolução do pacote anakin
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from fastapi import FastAPI, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from anakin.config.settings import Settings, get_settings
from anakin.web.data_service import AnakinDataService
from anakin.web.telemetry import registrar_evento, telemetry

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Ciclo de vida da aplicação FastAPI: emite telemetria de boot e inicialização."""
    registrar_evento(
        origem="WEB_SERVER",
        nivel="SUCCESS",
        mensagem="Servidor HUD Sci-Fi operacional. Endpoints REST e streaming SSE ativos.",
    )
    yield
    registrar_evento(
        origem="WEB_SERVER",
        nivel="INFO",
        mensagem="Servidor HUD Sci-Fi desligado com segurança.",
    )


app = FastAPI(
    title="A.N.A.K.I.N Sci-Fi HUD Console",
    description="Painel de Telemetria Operacional e Despacho de Chamados com Interface Sci-Fi",
    version="2.0.0",
    lifespan=lifespan,
)

# Habilita CORS para testes locais e integrações
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

data_service = AnakinDataService()


# -------------------------------------------------------------
# Rotas de Assets Estáticos
# -------------------------------------------------------------
@app.get("/", include_in_schema=False)
async def get_index() -> FileResponse:
    """Serve a interface principal do HUD."""
    index_path = STATIC_DIR / "index.html"
    return FileResponse(index_path)


# Mapeamento de pastas estáticas individuais para compatibilidade com caminhos relativos
if (STATIC_DIR / "css").exists():
    app.mount("/css", StaticFiles(directory=STATIC_DIR / "css"), name="css")
if (STATIC_DIR / "assets").exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")
if (STATIC_DIR / "js").exists():
    app.mount("/js", StaticFiles(directory=STATIC_DIR / "js"), name="js")
if (STATIC_DIR / "themes").exists():
    app.mount("/themes", StaticFiles(directory=STATIC_DIR / "themes"), name="themes")

# Montagem estática geral em /static
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# -------------------------------------------------------------
# Endpoints da API REST
# -------------------------------------------------------------
@app.get("/api/status")
async def get_status() -> dict[str, Any]:
    """Retorna métricas do Milvus, contadores do DuckDB e saúde da aplicação."""
    return data_service.obter_status_geral()


@app.get("/api/tickets")
async def get_tickets(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=15, ge=1, le=100),
    apenas_abertos: bool = Query(default=True),
    status: Optional[str] = Query(default=None),
    prioridade: Optional[str] = Query(default=None),
    tecnico: Optional[str] = Query(default=None),
    busca: Optional[str] = Query(default=None),
) -> dict[str, Any]:
    """Retorna os chamados abertos do DuckDB com paginação e ordenação por criticidade."""
    return data_service.obter_tickets(
        page=page,
        page_size=page_size,
        apenas_abertos=apenas_abertos,
        status_filtro=status,
        prioridade_filtro=prioridade,
        tecnico_filtro=tecnico,
        busca=busca,
    )


@app.get("/api/pool")
async def get_pool() -> dict[str, Any]:
    """Retorna o estado atual do Pool de Atendimento N1/N2 com os Scores de Carga."""
    return data_service.obter_pool_atendimento()


@app.get("/api/metrics")
async def get_metrics() -> dict[str, Any]:
    """Retorna as métricas cirúrgicas de SLA, volumetria por técnico, categorias e outliers."""
    return data_service.obter_metricas_cirurgicas()


@app.get("/api/logs")
async def get_logs(limite: int = Query(default=100, ge=1, le=500)) -> list[dict[str, Any]]:
    """Retorna o histórico recente de eventos da telemetria HUD."""
    return telemetry.obter_eventos(limite=limite)


@app.post("/api/logs")
async def post_log(request: Request) -> dict[str, Any]:
    """Permite que scripts externos (ex: dispatcher_aovivo) injetem eventos na telemetria."""
    corpo = await request.json()
    origem = corpo.get("origem", "EXTERNO")
    nivel = corpo.get("nivel", "INFO")
    mensagem = corpo.get("mensagem", "")
    detalhes = corpo.get("detalhes")

    ev = registrar_evento(origem=origem, nivel=nivel, mensagem=mensagem, detalhes=detalhes)
    return {"status": "ok", "evento": ev.to_dict()}


# -------------------------------------------------------------
# Streaming em Tempo Real via Server-Sent Events (SSE)
# -------------------------------------------------------------
@app.get("/api/stream")
async def stream_events(request: Request) -> StreamingResponse:
    """Canal contínuo SSE para envio de telemetria, novos eventos e status do sistema."""
    queue = telemetry.subscrever()

    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            # Envia status inicial imediatamente ao conectar
            status_inicial = data_service.obter_status_geral()
            yield f"event: status\ndata: {json.dumps(status_inicial, ensure_ascii=False)}\n\n"

            pool_inicial = data_service.obter_pool_atendimento()
            yield f"event: pool\ndata: {json.dumps(pool_inicial, ensure_ascii=False)}\n\n"

            # Envia logs recentes
            logs_iniciais = telemetry.obter_eventos(limite=20)
            yield f"event: logs_batch\ndata: {json.dumps(logs_iniciais, ensure_ascii=False)}\n\n"

            contador_heartbeat = 0

            while True:
                # Se o cliente desconectar, encerra o loop
                if await request.is_disconnected():
                    break

                try:
                    # Espera um novo evento com timeout curto para enviar heartbeats periódicos
                    ev = await asyncio.wait_for(queue.get(), timeout=2.0)
                    yield f"event: log\ndata: {json.dumps(ev.to_dict(), ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    pass

                contador_heartbeat += 1
                # A cada 4 segundos (~2 ciclos de 2s), transmite snapshot de status e pool
                if contador_heartbeat >= 2:
                    contador_heartbeat = 0
                    try:
                        st = data_service.obter_status_geral()
                        yield f"event: status\ndata: {json.dumps(st, ensure_ascii=False)}\n\n"
                    except Exception:
                        pass

        finally:
            telemetry.desinscrever(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def start_server(host: str = "127.0.0.1", port: int = 7100, reload: bool = False) -> None:
    """Inicia o servidor Uvicorn."""
    uvicorn.run(
        "anakin.web.server:app",
        host=host,
        port=port,
        reload=reload,
        log_level="info",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="A.N.A.K.I.N HUD Sci-Fi Server")
    parser.add_argument("--host", default="127.0.0.1", help="Host de escuta (padrão: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=7100, help="Porta de escuta (padrão: 7100)")
    parser.add_argument("--reload", action="store_true", help="Recarregamento automático (dev)")
    args = parser.parse_args()

    start_server(host=args.host, port=args.port, reload=args.reload)

