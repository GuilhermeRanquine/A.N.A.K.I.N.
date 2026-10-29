"""Testes unitários e de integração do módulo AutoDispatcher e atribuição ponderada de chamados."""

from datetime import datetime, timedelta
import unittest
from anakin.config.settings import Settings
from anakin.dispatch.auto_dispatcher import (
    AutoDispatcher,
    OperadorElegivel,
    ScoreOperador,
    OPERADORES_ELEGIVEIS,
    OPERADORES_BLOQUEADOS,
    SecurityDispatchError,
    verificar_seguranca_operador,
    eh_chamado_sem_tecnico,
)
from anakin.domain.ticket_models import TicketSummary
from anakin.milvus.ticket_creator_client import MilvusTicketCreatorClient, CreationSafetyError


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

    def test_bloqueio_por_nome_proibido(self) -> None:
        """Verifica se nomes da lista de bloqueio geram erro imediato."""
        proibidos_nomes = ["Paulo Roberto", "Leandro Oliveira", "Wander Cristiano", "Guilherme Ranquine"]
        for nome in proibidos_nomes:
            with self.assertRaises(SecurityDispatchError):
                verificar_seguranca_operador("email_qualquer@upgradeti.com.br", nome=nome)

    def test_identificacao_chamados_sem_tecnico(self) -> None:
        """Valida detecção precisa de chamados sem técnico atribuído."""
        t_sem_1 = TicketSummary(codigo="101", tecnico="Não atribuído", email_tecnico=None)
        t_sem_2 = TicketSummary(codigo="102", tecnico="", email_tecnico="")
        t_sem_3 = TicketSummary(codigo="103", tecnico=None, email_tecnico=None)
        t_sem_4 = TicketSummary(codigo="104", tecnico="sem tecnico", email_tecnico=None)

        t_com_1 = TicketSummary(codigo="201", tecnico="Kaio Silva", email_tecnico="kaio.silva@upgradeti.com.br")
        t_com_2 = TicketSummary(codigo="202", tecnico="Paulo Roberto", email_tecnico="paulo@upgradeti.com.br")

        self.assertTrue(eh_chamado_sem_tecnico(t_sem_1))
        self.assertTrue(eh_chamado_sem_tecnico(t_sem_2))
        self.assertTrue(eh_chamado_sem_tecnico(t_sem_3))
        self.assertTrue(eh_chamado_sem_tecnico(t_sem_4))

        self.assertFalse(eh_chamado_sem_tecnico(t_com_1))
        self.assertFalse(eh_chamado_sem_tecnico(t_com_2))

    def test_calculo_formula_score_ponderado(self) -> None:
        """Testa o cálculo da fórmula: (Atendendo * 3) + (A Fazer * 2) + (Pausado * 1) + (Outros * 1)."""
        sc = ScoreOperador(
            operador=OPERADORES_ELEGIVEIS[0],
            qtd_atendendo=2,  # 2 * 3 = 6
            qtd_a_fazer=1,    # 1 * 2 = 2
            qtd_pausado=5,    # 5 * 1 = 5
            qtd_outros=1,     # 1 * 1 = 1
            score_total=(2 * 3) + (1 * 2) + (5 * 1) + (1 * 1),  # 14
        )
        self.assertEqual(sc.score_total, 14)
        self.assertEqual(sc.detalhe_formatado, "2 fazendo, 1 a fazer, 5 pausado, 1 outros")

    def test_selecao_menor_score_ponderado(self) -> None:
        """Valida se o operador com o menor Score é escolhido, mesmo com mais chamados brutos pausados."""
        disp = AutoDispatcher()
        # Operador A: 2 atendendo (6 pts) + 0 pausado = 6 pts (Total = 2 chamados)
        # Operador B: 0 atendendo + 4 pausados (4 pts) = 4 pts (Total = 4 chamados)
        # B tem mais chamados, mas score menor porque são todos pausados!
        disp.calcular_scores_pool = lambda: {  # type: ignore
            OPERADORES_ELEGIVEIS[0]: ScoreOperador(OPERADORES_ELEGIVEIS[0], qtd_atendendo=2, qtd_pausado=0, score_total=6),
            OPERADORES_ELEGIVEIS[1]: ScoreOperador(OPERADORES_ELEGIVEIS[1], qtd_atendendo=0, qtd_pausado=4, score_total=4),
            OPERADORES_ELEGIVEIS[2]: ScoreOperador(OPERADORES_ELEGIVEIS[2], qtd_atendendo=3, qtd_pausado=5, score_total=14),
            OPERADORES_ELEGIVEIS[3]: ScoreOperador(OPERADORES_ELEGIVEIS[3], qtd_atendendo=1, qtd_pausado=10, score_total=13),
            OPERADORES_ELEGIVEIS[4]: ScoreOperador(OPERADORES_ELEGIVEIS[4], qtd_atendendo=2, qtd_pausado=6, score_total=12),
            OPERADORES_ELEGIVEIS[5]: ScoreOperador(OPERADORES_ELEGIVEIS[5], qtd_atendendo=1, qtd_pausado=5, score_total=8),
        }

        op, sc, _ = disp.selecionar_operador_menor_score()
        self.assertEqual(op.nome, "Gabriel Oliveira")
        self.assertEqual(sc.score_total, 4)

    def test_desempate_por_ultima_atribuicao(self) -> None:
        """Valida que em empate de Score, quem recebeu chamado há mais tempo tem prioridade."""
        disp = AutoDispatcher()
        agora = datetime.now()
        # Ambos com Score 5, mas Operador 0 recebeu há 2 horas e Operador 1 recebeu há 10 minutos
        disp.calcular_scores_pool = lambda: {  # type: ignore
            OPERADORES_ELEGIVEIS[0]: ScoreOperador(
                OPERADORES_ELEGIVEIS[0],
                score_total=5,
                ultima_atribuicao=agora - timedelta(hours=2),
            ),
            OPERADORES_ELEGIVEIS[1]: ScoreOperador(
                OPERADORES_ELEGIVEIS[1],
                score_total=5,
                ultima_atribuicao=agora - timedelta(minutes=10),
            ),
            OPERADORES_ELEGIVEIS[2]: ScoreOperador(OPERADORES_ELEGIVEIS[2], score_total=10),
            OPERADORES_ELEGIVEIS[3]: ScoreOperador(OPERADORES_ELEGIVEIS[3], score_total=10),
            OPERADORES_ELEGIVEIS[4]: ScoreOperador(OPERADORES_ELEGIVEIS[4], score_total=10),
            OPERADORES_ELEGIVEIS[5]: ScoreOperador(OPERADORES_ELEGIVEIS[5], score_total=10),
        }

        op, sc, _ = disp.selecionar_operador_menor_score()
        # Carlos Eduardo recebeu há 2 horas (mais tempo atrás), deve ser o escolhido
        self.assertEqual(op.nome, "Carlos Eduardo")
        self.assertEqual(sc.score_total, 5)

    def test_desempate_round_robin_quando_datas_iguais(self) -> None:
        """Valida desempate com Round-Robin quando scores e datas são idênticos."""
        disp = AutoDispatcher()
        disp.calcular_scores_pool = lambda: {  # type: ignore
            OPERADORES_ELEGIVEIS[0]: ScoreOperador(OPERADORES_ELEGIVEIS[0], score_total=4, ultima_atribuicao=None),
            OPERADORES_ELEGIVEIS[1]: ScoreOperador(OPERADORES_ELEGIVEIS[1], score_total=4, ultima_atribuicao=None),
            OPERADORES_ELEGIVEIS[2]: ScoreOperador(OPERADORES_ELEGIVEIS[2], score_total=10),
            OPERADORES_ELEGIVEIS[3]: ScoreOperador(OPERADORES_ELEGIVEIS[3], score_total=10),
            OPERADORES_ELEGIVEIS[4]: ScoreOperador(OPERADORES_ELEGIVEIS[4], score_total=10),
            OPERADORES_ELEGIVEIS[5]: ScoreOperador(OPERADORES_ELEGIVEIS[5], score_total=10),
        }

        op1, _, _ = disp.selecionar_operador_menor_score()
        op2, _, _ = disp.selecionar_operador_menor_score()
        op3, _, _ = disp.selecionar_operador_menor_score()

        self.assertEqual([op1.nome, op2.nome, op3.nome], ["Carlos Eduardo", "Gabriel Oliveira", "Carlos Eduardo"])

    def test_validacao_rota_atualizar_ticket_creator(self) -> None:
        """Testa se POST /chamado/atualizar está liberado no MilvusTicketCreatorClient."""
        creator = MilvusTicketCreatorClient()
        creator._validar_rota("POST", "/chamado/atualizar")
        creator._validar_rota("POST", "/chamado/criar")
        creator._validar_rota("GET", "/cliente/busca")

        with self.assertRaises(CreationSafetyError):
            creator._validar_rota("DELETE", "/chamado/deletar")
        with self.assertRaises(CreationSafetyError):
            creator._validar_rota("PUT", "/cliente/editar")


if __name__ == "__main__":
    unittest.main()
