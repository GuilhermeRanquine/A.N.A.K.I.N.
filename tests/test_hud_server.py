"""Testes unitários e de integração para o servidor web do HUD Sci-Fi."""

import sys
from pathlib import Path
import unittest

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from fastapi.testclient import TestClient
from anakin.web.server import app


class TestHudServer(unittest.TestCase):
    """Bateria de testes das rotas do servidor HUD Sci-Fi."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)

    def test_rota_raiz_serve_index_html(self) -> None:
        """Verifica se a rota raiz (/) serve o index.html com o título esperado."""
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("A.N.A.K.I.N // OPERATIONAL HUD CONSOLE", resp.text)
        self.assertIn("css/hud.css", resp.text)
        self.assertIn("js/hud-app.js", resp.text)

    def test_rotas_estaticas_acessiveis(self) -> None:
        """Verifica se os arquivos estáticos de CSS, JS e fontes são servidos corretamente."""
        resp_css = self.client.get("/css/hud.css")
        self.assertEqual(resp_css.status_code, 200)
        self.assertIn("--bg:", resp_css.text)

        resp_components = self.client.get("/css/hud-components.css")
        self.assertEqual(resp_components.status_code, 200)
        self.assertIn(".hud-btn", resp_components.text)

        resp_js = self.client.get("/js/hud-app.js")
        self.assertEqual(resp_js.status_code, 200)
        self.assertIn("carregarStatus", resp_js.text)

    def test_api_status_readonly(self) -> None:
        """Verifica se /api/status retorna os contadores do DuckDB e a trava Read-Only ativa."""
        resp = self.client.get("/api/status")
        self.assertEqual(resp.status_code, 200)
        dados = resp.json()

        self.assertTrue(dados.get("read_only_ativo"), "O HUD deve obrigatoriamente operar com read_only_ativo=True")
        self.assertIn("kpis", dados)
        kpis = dados["kpis"]
        self.assertIn("total_tickets", kpis)
        self.assertIn("total_abertos", kpis)
        self.assertIn("taxa_conformidade_pct", kpis)
        self.assertGreater(kpis["total_tickets"], 0)

    def test_api_tickets_paginacao_e_criticidade(self) -> None:
        """Verifica se /api/tickets retorna chamados paginados e com campos normativos."""
        resp = self.client.get("/api/tickets?page=1&page_size=5")
        self.assertEqual(resp.status_code, 200)
        dados = resp.json()

        self.assertEqual(dados["page"], 1)
        self.assertEqual(dados["page_size"], 5)
        self.assertIn("itens", dados)
        self.assertLessEqual(len(dados["itens"]), 5)

        if dados["itens"]:
            primeiro = dados["itens"][0]
            self.assertIn("codigo", primeiro)
            self.assertIn("prioridade_norm", primeiro)
            self.assertIn("sla_solucao_norm", primeiro)
            self.assertIn("tempo_aberto", primeiro)

    def test_api_pool_atendimento(self) -> None:
        """Verifica se /api/pool retorna os operadores oficiais e seus scores."""
        resp = self.client.get("/api/pool")
        self.assertEqual(resp.status_code, 200)
        dados = resp.json()

        self.assertIn("operadores", dados)
        self.assertEqual(len(dados["operadores"]), 6, "O pool deve conter exatamente 6 operadores oficiais")

        # Verifica presença de campos esperados
        nomes = [op["nome"] for op in dados["operadores"]]
        self.assertIn("Carlos Eduardo", nomes)
        self.assertIn("Gabriel Sena", nomes)

    def test_api_metrics(self) -> None:
        """Verifica se /api/metrics retorna volumetria por SLA, prioridades e outliers."""
        resp = self.client.get("/api/metrics")
        self.assertEqual(resp.status_code, 200)
        dados = resp.json()

        self.assertIn("prioridades", dados)
        self.assertIn("categorias", dados)
        self.assertIn("sla", dados)
        self.assertIn("outliers_clientes", dados)

    def test_api_logs_e_injecao_externa(self) -> None:
        """Verifica se /api/logs lista eventos e aceita novos registros via POST."""
        # 1. Injeta um log de teste
        payload = {
            "origem": "TEST_UNIT",
            "nivel": "INFO",
            "mensagem": "Verificação automatizada do HUD Console executada com sucesso.",
        }
        post_resp = self.client.post("/api/logs", json=payload)
        self.assertEqual(post_resp.status_code, 200)

        # 2. Consulta histórico de logs
        get_resp = self.client.get("/api/logs?limite=10")
        self.assertEqual(get_resp.status_code, 200)
        logs = get_resp.json()
        self.assertIsInstance(logs, list)
        self.assertGreater(len(logs), 0)

        mensagens = [l["mensagem"] for l in logs]
        self.assertIn("Verificação automatizada do HUD Console executada com sucesso.", mensagens)


if __name__ == "__main__":
    unittest.main()

