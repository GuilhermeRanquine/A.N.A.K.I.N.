"""Testes automatizados do Ciclo de Vida Unificado, Worker de Despacho e Streaming SSE."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any
import asyncio
import json
import sys
import unittest
from unittest.mock import AsyncMock, patch

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from fastapi.testclient import TestClient

from anakin.config.settings import Settings, get_settings
from anakin.dispatch.auto_dispatcher import (
    AutoDispatcher,
    OPERADORES_ELEGIVEIS,
    OperadorElegivel,
)
from anakin.dispatch.dispatcher_worker import DispatcherWorker
from anakin.domain.ticket_models import TicketSummary
from anakin.storage.local_cache import LocalCache
from anakin.web.server import app
from anakin.web.telemetry import TelemetryEvent, registrar_evento, telemetry


class TestUnifiedLifecycle(unittest.TestCase):
    """Bateria de testes do ciclo de vida unificado (FastAPI lifespan + background worker)."""

    def setUp(self) -> None:
        self.settings = get_settings()

    def test_worker_inicializacao_e_atributos(self) -> None:
        """Verifica se o DispatcherWorker inicializa com os atributos corretos de estado."""
        worker = DispatcherWorker(
            settings=self.settings,
            simulacao=True,
            intervalo_segundos=30,
        )
        self.assertFalse(worker.is_running)
        self.assertFalse(worker.is_paused)
        self.assertEqual(worker.intervalo_segundos, 30)
        self.assertEqual(worker.segundos_restantes, 30)
        self.assertTrue(worker.modo_simulacao)

        status = worker.get_status()
        self.assertEqual(status["rodando"], False)
        self.assertEqual(status["pausado"], False)
        self.assertEqual(status["intervalo_segundos"], 30)
        self.assertTrue(status["modo_simulacao"])

    def test_worker_pause_e_resume(self) -> None:
        """Valida que pause() e resume() alteram o estado e emitem telemetria."""
        worker = DispatcherWorker(
            settings=self.settings,
            simulacao=True,
            intervalo_segundos=10,
        )
        worker.pause()
        self.assertTrue(worker.is_paused)
        self.assertFalse(worker._pause_event.is_set())

        worker.resume()
        self.assertFalse(worker.is_paused)
        self.assertTrue(worker._pause_event.is_set())

    def test_lifespan_starts_and_stops_worker_gracefully(self) -> None:
        """Valida que a aplicação sobe com o worker ativo e encerra com cancelamento gracioso."""
        app.state.simulacao = True
        app.state.intervalo = 60
        app.state.sem_despachante = False

        # Usamos patch na calibração inicial e listagem do Milvus para isolamento do teste
        with patch.object(
            DispatcherWorker,
            "_calibracao_inicial",
            new_callable=AsyncMock,
        ) as mock_calib, patch.object(
            DispatcherWorker,
            "_executar_ciclo",
            new_callable=AsyncMock,
        ) as mock_ciclo:
            mock_calib.return_value = None
            # Simula um ciclo que dorme brevemente
            async def dummy_ciclo():
                await asyncio.sleep(0.05)

            mock_ciclo.side_effect = dummy_ciclo

            with TestClient(app) as client:
                # Verifica se worker foi criado e está rodando
                self.assertTrue(hasattr(app.state, "dispatcher_worker"))
                worker: DispatcherWorker = app.state.dispatcher_worker
                self.assertTrue(worker.is_running)

                # Testa endpoint de status
                resp_status = client.get("/api/dispatcher/status")
                self.assertEqual(resp_status.status_code, 200)
                dados_status = resp_status.json()
                self.assertTrue(dados_status["rodando"])
                self.assertTrue(dados_status["modo_simulacao"])

                # Testa endpoint de pausa
                resp_pause = client.post("/api/dispatcher/pause")
                self.assertEqual(resp_pause.status_code, 200)
                dados_pause = resp_pause.json()
                self.assertEqual(dados_pause["status"], "ok")
                self.assertTrue(worker.is_paused)

                # Testa endpoint de retomada
                resp_resume = client.post("/api/dispatcher/resume")
                self.assertEqual(resp_resume.status_code, 200)
                dados_resume = resp_resume.json()
                self.assertEqual(dados_resume["status"], "ok")
                self.assertFalse(worker.is_paused)

            # Ao sair do bloco 'with TestClient', o lifespan executa shutdown gracioso
            self.assertFalse(worker.is_running, "Worker deve estar encerrado após shutdown do lifespan")

    def test_modo_sem_despachante(self) -> None:
        """Verifica comportamento com a flag --sem-despachante ativada."""
        app.state.sem_despachante = True
        app.state.simulacao = True
        app.state.intervalo = 62

        with TestClient(app) as client:
            worker: DispatcherWorker = app.state.dispatcher_worker
            self.assertFalse(worker.is_running, "Worker não deve ter iniciado com sem_despachante=True")

            resp = client.get("/api/dispatcher/status")
            self.assertEqual(resp.status_code, 200)
            self.assertFalse(resp.json()["rodando"])

        # Restaura flag
        app.state.sem_despachante = False


class TestSSEStreaming(unittest.TestCase):
    """Testes da rota e canal SSE (/api/stream) com eventos de log e countdown."""

    def test_sse_recebe_evento_countdown_isolado(self) -> None:
        """Verifica se eventos com tipo 'countdown' são capturados pela fila de telemetria."""
        queue = telemetry.subscrever()
        try:
            ev = registrar_evento(
                origem="DISPATCHER",
                nivel="DEBUG",
                mensagem="Contagem regressiva: 45s",
                detalhes={"tipo": "countdown", "segundos": 45, "total": 62},
            )
            # Verifica que o evento entrou na fila
            evento_recebido = queue.get_nowait()
            self.assertEqual(evento_recebido.origem, "DISPATCHER")
            self.assertEqual(evento_recebido.detalhes.get("tipo"), "countdown")
            self.assertEqual(evento_recebido.detalhes.get("segundos"), 45)
        finally:
            telemetry.desinscrever(queue)

    def test_sse_endpoint_conexao(self) -> None:
        """Verifica se o endpoint /api/stream abre stream de texto e emite cabeçalhos corretos."""
        app.state.sem_despachante = True
        client = TestClient(app)
        # Faz uma requisição com stream e lê os primeiros chunks
        with client.stream("GET", "/api/stream") as response:
            self.assertEqual(response.status_code, 200)
            self.assertIn("text/event-stream", response.headers["content-type"])
            # Lê o primeiro evento emitido na conexão
            chunks = []
            for chunk in response.iter_lines():
                if chunk:
                    chunks.append(chunk)
                if len(chunks) >= 2:
                    break

            texto_stream = "\n".join(chunks)
            self.assertIn("event:", texto_stream)


class TestDuckDBConcurrency(unittest.TestCase):
    """Garante que consultas repetidas no HUD não sofram bloqueio por lock durante o despacho."""

    @classmethod
    def setUpClass(cls) -> None:
        app.state.sem_despachante = True
        cls.client = TestClient(app)
        cls.settings = get_settings()
        cls.cache = LocalCache(settings=cls.settings)
        cls.dispatcher = AutoDispatcher(settings=cls.settings, cache=cls.cache)

    def test_concorrencia_leitura_e_atualizacao(self) -> None:
        """Executa escritas de despacho e múltiplas leituras simultâneas sem deadlocks."""
        operador = OPERADORES_ELEGIVEIS[0]

        def ler_tickets() -> int:
            resp = self.client.get("/api/tickets?page=1&page_size=10")
            return resp.status_code

        def ler_pool() -> int:
            resp = self.client.get("/api/pool")
            return resp.status_code

        def ler_status() -> int:
            resp = self.client.get("/api/status")
            return resp.status_code

        def escrever_despacho(i: int) -> None:
            # Simula atualização rápida no DuckDB de um chamado fictício
            self.dispatcher._atualizar_chamado_duckdb(
                chamado_id_ou_codigo=f"9999{i}",
                operador=operador,
                prioridade="Médio",
            )

        # Executa em paralelo com ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=8) as executor:
            leituras_futures = []
            for _ in range(15):
                leituras_futures.append(executor.submit(ler_tickets))
                leituras_futures.append(executor.submit(ler_pool))
                leituras_futures.append(executor.submit(ler_status))

            escritas_futures = [
                executor.submit(escrever_despacho, i) for i in range(5)
            ]

            # Todas as leituras devem retornar 200
            for f in leituras_futures:
                status_code = f.result()
                self.assertEqual(status_code, 200)

            # Todas as escritas devem ter completado sem erro
            for f in escritas_futures:
                f.result()


if __name__ == "__main__":
    unittest.main()

