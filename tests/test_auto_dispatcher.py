"""Testes unitários e de integração do módulo AutoDispatcher, triagem por IA e roteamento por nível."""

from datetime import datetime, timedelta
import unittest
from anakin.config.settings import Settings
from anakin.dispatch.auto_dispatcher import (
    AutoDispatcher,
    OperadorElegivel,
    ScoreOperador,
    OPERADORES_ELEGIVEIS,
    OPERADORES_BLOQUEADOS,
    POOL_SENIOR_N2_EMAILS,
    POOL_PLENO_N1_EMAILS,
    SecurityDispatchError,
    verificar_seguranca_operador,
    eh_chamado_sem_tecnico,
)
from anakin.dispatch.ticket_triage_ai import (
    TicketTriageAI,
    ResultadoTriagemIA,
    normalizar_prioridade,
    normalizar_tipo_ticket,
)
from anakin.domain.ticket_models import TicketSummary
from anakin.milvus.ticket_creator_client import MilvusTicketCreatorClient, CreationSafetyError
from anakin.storage.local_cache import LocalCache


class TestAutoDispatcher(unittest.TestCase):
    def test_pool_elegivel_contem_apenas_seis_operadores(self) -> None:
        """Valida se o pool é formado estritamente pelos 6 operadores autorizados."""
        self.assertEqual(len(OPERADORES_ELEGIVEIS), 6)
        emails_esperados = {
            "carlos.bucazio@upgradeti.com.br",
            "gabriel@upgradeti.com.br",
            "gabryel.santos@upgradeti.com.br",
            "gabriel.sena@upgradeti.com.br",
            "guilherme.melo@upgradeti.com.br",
            "kaio.silva@upgradeti.com.br",
        }
        emails_atuais = {op.email for op in OPERADORES_ELEGIVEIS}
        self.assertEqual(emails_atuais, emails_esperados)

    def test_divisao_pools_senior_e_pleno(self) -> None:
        """Valida a separação dos dois pools de atendimento por nível."""
        self.assertEqual(len(POOL_SENIOR_N2_EMAILS), 3)
        self.assertEqual(len(POOL_PLENO_N1_EMAILS), 3)

        self.assertIn("gabriel@upgradeti.com.br", POOL_SENIOR_N2_EMAILS)
        self.assertIn("gabryel.santos@upgradeti.com.br", POOL_SENIOR_N2_EMAILS)
        self.assertIn("guilherme.melo@upgradeti.com.br", POOL_SENIOR_N2_EMAILS)

        self.assertIn("carlos.bucazio@upgradeti.com.br", POOL_PLENO_N1_EMAILS)
        self.assertIn("kaio.silva@upgradeti.com.br", POOL_PLENO_N1_EMAILS)
        self.assertIn("gabriel.sena@upgradeti.com.br", POOL_PLENO_N1_EMAILS)

    def test_bloqueio_rigido_operadores_proibidos(self) -> None:
        """Garante que operadores proibidos não possam receber chamados sob nenhuma hipótese."""
        proibidos_emails = [
            "paulo@upgradeti.com.br",
            "leandro@upgradeti.com.br",
            "analista05@upgradeti.com.br",
            "felipe.xavier@upgradeti.com.br",
            "willian.pereira@upgradeti.com.br",
            "aijalom.goncalves@upgradeti.com.br",
            "guilherme.ranquine@upgradeti.com.br",
        ]
        for email in proibidos_emails:
            with self.assertRaises(SecurityDispatchError):
                verificar_seguranca_operador(email)

    def test_roteamento_por_prioridade_alta_e_critica(self) -> None:
        """Chamados Críticos ou Altos devem ser atribuídos ao Pool Sênior / N2."""
        disp = AutoDispatcher()
        # Mock das cargas
        disp.calcular_scores_pool = lambda: {  # type: ignore
            OPERADORES_ELEGIVEIS[0]: ScoreOperador(OPERADORES_ELEGIVEIS[0], score_total=2),   # Carlos (Pleno)
            OPERADORES_ELEGIVEIS[1]: ScoreOperador(OPERADORES_ELEGIVEIS[1], score_total=10),  # Gabriel O. (Senior)
            OPERADORES_ELEGIVEIS[2]: ScoreOperador(OPERADORES_ELEGIVEIS[2], score_total=6),   # Gabryel (Senior - menor do senior)
            OPERADORES_ELEGIVEIS[3]: ScoreOperador(OPERADORES_ELEGIVEIS[3], score_total=0),   # Gabriel Sena (Pleno)
            OPERADORES_ELEGIVEIS[4]: ScoreOperador(OPERADORES_ELEGIVEIS[4], score_total=8),   # Guilherme M. (Senior)
            OPERADORES_ELEGIVEIS[5]: ScoreOperador(OPERADORES_ELEGIVEIS[5], score_total=4),   # Kaio (Pleno)
        }

        # Mesmo Gabriel Sena tendo score 0, o chamado Crítico deve ir para o Sênior de menor score (Gabryel Santos com 6)
        op_critico, sc_critico, _, tag_critico = disp.selecionar_operador_por_prioridade("Crítico")
        self.assertEqual(op_critico.nome, "Gabryel Santos")
        self.assertEqual(sc_critico.score_total, 6)
        self.assertIn("Sênior", tag_critico)

        op_alto, sc_alto, _, tag_alto = disp.selecionar_operador_por_prioridade("Alto")
        self.assertEqual(op_alto.nome, "Gabryel Santos")
        self.assertIn("Sênior", tag_alto)

    def test_roteamento_por_prioridade_media_e_baixa(self) -> None:
        """Chamados Médios ou Baixos devem ser atribuídos ao Pool Pleno / N1."""
        disp = AutoDispatcher()
        disp.calcular_scores_pool = lambda: {  # type: ignore
            OPERADORES_ELEGIVEIS[0]: ScoreOperador(OPERADORES_ELEGIVEIS[0], score_total=5),   # Carlos (Pleno)
            OPERADORES_ELEGIVEIS[1]: ScoreOperador(OPERADORES_ELEGIVEIS[1], score_total=2),   # Gabriel O. (Senior com menor score global)
            OPERADORES_ELEGIVEIS[2]: ScoreOperador(OPERADORES_ELEGIVEIS[2], score_total=6),   # Gabryel (Senior)
            OPERADORES_ELEGIVEIS[3]: ScoreOperador(OPERADORES_ELEGIVEIS[3], score_total=3),   # Gabriel Sena (Pleno - menor do pleno)
            OPERADORES_ELEGIVEIS[4]: ScoreOperador(OPERADORES_ELEGIVEIS[4], score_total=8),   # Guilherme M. (Senior)
            OPERADORES_ELEGIVEIS[5]: ScoreOperador(OPERADORES_ELEGIVEIS[5], score_total=4),   # Kaio (Pleno)
        }

        # Mesmo Gabriel O. tendo score 2, chamado Baixo deve ir para o Pleno de menor score (Gabriel Sena com 3)
        op_baixo, sc_baixo, _, tag_baixo = disp.selecionar_operador_por_prioridade("Baixo")
        self.assertEqual(op_baixo.nome, "Gabriel Sena")
        self.assertEqual(sc_baixo.score_total, 3)
        self.assertIn("Pleno", tag_baixo)

        op_medio, sc_medio, _, tag_medio = disp.selecionar_operador_por_prioridade("Médio")
        self.assertEqual(op_medio.nome, "Gabriel Sena")
        self.assertIn("Pleno", tag_medio)

    def test_catalogo_categorias_e_tipos(self) -> None:
        """Valida consulta do catálogo real no DuckDB."""
        cache = LocalCache()
        cat = cache.obter_catalogo_categorias_e_tipos()
        self.assertIn("tipos", cat)
        self.assertIn("categorias", cat)
        self.assertTrue(len(cat["categorias"]) > 0)
        self.assertTrue(len(cat["texto_formatado"]) > 0)

    def test_triagem_ia_fallback_heuristico(self) -> None:
        """Testa o fallback heurístico com extração correta de prioridade, tipo e contato."""
        triage = TicketTriageAI()
        t = TicketSummary(
            codigo="888",
            cliente="Farmacia Central",
            assunto="Configurar Outlook e impressora no computador da recepcao",
            descricao="Favor instalar a impressora HP e configurar o email recepcao@farmacia.com. Solicitante: Carlos Alberto, tel 21 9999-8888",
        )
        res = triage._fallback_heuristico(t, catalogo={})
        self.assertEqual(res.prioridade, "Baixo")
        self.assertEqual(res.tipo_ticket, "Requisição")
        self.assertEqual(res.contato_email, "recepcao@farmacia.com")
        self.assertEqual(res.contato_telefone, "21 9999-8888")

    def test_fallback_balanceado_sob_sobrecarga(self) -> None:
        """Valida que quando o pool alvo está sob sobrecarga severa, ativa fallback balanceado."""
        disp = AutoDispatcher()
        # Pool Sênior totalmente sobrecarregado (scores >= 35) e Pleno com score baixo
        disp.calcular_scores_pool = lambda: {  # type: ignore
            OPERADORES_ELEGIVEIS[0]: ScoreOperador(OPERADORES_ELEGIVEIS[0], score_total=8),   # Carlos (Pleno)
            OPERADORES_ELEGIVEIS[1]: ScoreOperador(OPERADORES_ELEGIVEIS[1], score_total=40),  # Gabriel O. (Senior sobrecarregado)
            OPERADORES_ELEGIVEIS[2]: ScoreOperador(OPERADORES_ELEGIVEIS[2], score_total=36),  # Gabryel (Senior sobrecarregado)
            OPERADORES_ELEGIVEIS[3]: ScoreOperador(OPERADORES_ELEGIVEIS[3], score_total=5),   # Gabriel Sena (Pleno livre)
            OPERADORES_ELEGIVEIS[4]: ScoreOperador(OPERADORES_ELEGIVEIS[4], score_total=38),  # Guilherme M. (Senior sobrecarregado)
            OPERADORES_ELEGIVEIS[5]: ScoreOperador(OPERADORES_ELEGIVEIS[5], score_total=6),   # Kaio (Pleno)
        }

        # Chamado Alto deveria ir para Sênior, mas como menor Sênior tem 36 (>=35) e menor Pleno tem 5 (folga >= 10), ativa fallback!
        op, sc, _, tag = disp.selecionar_operador_por_prioridade("Alto")
        self.assertEqual(op.nome, "Gabriel Sena")
        self.assertEqual(sc.score_total, 5)
        self.assertIn("Fallback", tag)

    def test_mapeamento_numerico_prioridade(self) -> None:
        """Valida que a prioridade é estritamente mapeada para '1', '2', '3' ou '4'."""
        from anakin.milvus.ticket_creator_client import resolver_prioridade_id, PRIORIDADE_ID_MAP

        self.assertEqual(resolver_prioridade_id("Baixo"), "1")
        self.assertEqual(resolver_prioridade_id("baixa"), "1")
        self.assertEqual(resolver_prioridade_id("Médio"), "2")
        self.assertEqual(resolver_prioridade_id("medio"), "2")
        self.assertEqual(resolver_prioridade_id("Alto"), "3")
        self.assertEqual(resolver_prioridade_id("alta"), "3")
        self.assertEqual(resolver_prioridade_id("Crítico"), "4")
        self.assertEqual(resolver_prioridade_id("critico"), "4")

        # IDs diretos
        self.assertEqual(resolver_prioridade_id("1"), "1")
        self.assertEqual(resolver_prioridade_id("4"), "4")

    def test_triade_atribuicao_e_ordem_payload(self) -> None:
        """Valida se Mesa, Setor e Operador são definidos no mesmo técnico e na ordem correta."""
        creator = MilvusTicketCreatorClient()
        # Mock de httpx client para capturar o payload gerado
        payload_capturado = {}

        class DummyClient:
            async def post(self, url, json=None, **kwargs):
                nonlocal payload_capturado
                payload_capturado = json
                class DummyResp:
                    status_code = 200
                    def raise_for_status(self): pass
                    def json(self): return {"status": True}
                return DummyResp()

        async def dummy_client():
            return DummyClient()

        async def dummy_wait():
            pass

        creator._obter_client = dummy_client  # type: ignore
        creator._aguardar_rate_limit = dummy_wait  # type: ignore

        import asyncio
        asyncio.run(
            creator.atualizar_triagem_chamado(
                chamado_codigo_ou_id="77777",
                prioridade="Médio",
                tipo_ticket="Incidente",
                categoria_primaria="Computadores",
                categoria_secundaria="Lentidão",
                nome_operador="Gabriel Sena",
                email_tecnico="gabriel.sena@upgradeti.com.br",
            )
        )

        # 1. Validação da tríade com o mesmo técnico
        self.assertEqual(payload_capturado["chamado_mesa"], "Gabriel Sena")
        self.assertEqual(payload_capturado["chamado_setor"], "Gabriel Sena")
        self.assertEqual(payload_capturado["chamado_tecnico"], "gabriel.sena@upgradeti.com.br")

        # 2. Prioridade numérica obrigatória
        self.assertEqual(payload_capturado["chamado_prioridade"], "2")

        # 3. Ordem das chaves na serialização
        chaves = list(payload_capturado.keys())
        idx_mesa = chaves.index("chamado_mesa")
        idx_setor = chaves.index("chamado_setor")
        idx_tecnico = chaves.index("chamado_tecnico")
        idx_prioridade = chaves.index("chamado_prioridade")

        self.assertLess(idx_mesa, idx_tecnico, "chamado_mesa deve vir ANTES de chamado_tecnico")
        self.assertLess(idx_setor, idx_tecnico, "chamado_setor deve vir ANTES de chamado_tecnico")
        self.assertLess(idx_tecnico, idx_prioridade, "chamado_tecnico deve vir ANTES de chamado_prioridade")

    def test_validacao_rota_atualizar_ticket_creator(self) -> None:
        """Testa se POST /chamado/atualizar está liberado no MilvusTicketCreatorClient."""
        creator = MilvusTicketCreatorClient()
        creator._validar_rota("POST", "/chamado/atualizar")
        creator._validar_rota("POST", "/chamado/criar")
        creator._validar_rota("GET", "/cliente/busca")

        with self.assertRaises(CreationSafetyError):
            creator._validar_rota("DELETE", "/chamado/deletar")


if __name__ == "__main__":
    unittest.main()
