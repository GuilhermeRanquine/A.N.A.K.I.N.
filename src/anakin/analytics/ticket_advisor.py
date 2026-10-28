"""Motor Consultor e Copiloto do Ticket Manager (ITIL / Service Desk).

Fornece entendimento profundo de chamados, auditoria de histórico/comentários,
diagnóstico de gargalos, sugestões cirúrgicas de modificação no Milvus e
orientação prática de como operar cada técnico da Upgrade TI (100% Somente Leitura).

Conta com cascata resiliente de IA:
gemini-3.5-flash-lite -> gemini-3.1-flash-lite -> NVIDIA NIM (minimaxai/minimax-m3.1-flash).
"""

from pathlib import Path
from typing import Any, Optional
import json
import re
import httpx
import pandas as pd
from google import genai
from rich.console import Console

from anakin.config.settings import Settings, get_settings
from anakin.analytics.surgical_metrics import normalizar_sla_texto, normalizar_prioridade_texto

console = Console()

SYSTEM_INSTRUCTION_TICKET_ADVISOR = (
    "Você é o Copiloto do Ticket Manager e Gestor Sênior de Service Desk da Upgrade TI, "
    "com certificação ITIL v4 e COBIT. Sua missão é fornecer análises cirúrgicas, profundas e "
    "executivas sobre chamados de suporte técnico do sistema Milvus Helpdesk e sobre a carteira de chamados "
    "dos operadores, orientando o Ticket Manager a tomar decisões rápidas e operar seus técnicos com máxima eficácia.\n\n"
    "DIRETRIZES FUNDAMENTAIS DE POSTURA E AUDITORIA:\n"
    "1. PRECISÃO CIRÚRGICA E FIDELIDADE TEMPORAL: Baseie-se estritamente nos dados reais do chamado. "
    "NUNCA altere anos, datas ou horários fornecidos nos logs e metadados (se os logs são de 2026, mantenha estritamente 2026). "
    "NUNCA invente inconsistências cadastrais de status: os Metadados Oficiais da API são a fonte única e absoluta da verdade.\n"
    "2. DIFERENCIAÇÃO DE REGISTROS: Diferencie com clareza o que é relato do cliente, notas internas privadas de técnicos, "
    "comentários públicos e logs automáticos do sistema. Reconheça evidências visuais (prints anexados).\n"
    "3. ADAPTAÇÃO AO STATUS DO CHAMADO:\n"
    "   - Se o chamado estiver FINALIZADO/RESOLVIDO: atue como Auditor de Qualidade (QA), avaliando o tempo de atendimento, "
    "SLA, avaliação do cliente (estrelas) e qualidade documental. NUNCA prescreva comandos de troubleshooting nem sugira "
    "incomodar o cliente se o chamado já foi concluído e bem avaliado. Reconheça a excelência do operador.\n"
    "   - Se o chamado estiver ABERTO: foque em destravar a resolução técnica agora, propondo procedimentos exatos, garantindo "
    "governança (Mesa, Prioridade, Pausa) e orientando a cobrança do técnico para não estourar o SLA.\n"
    "4. OPERAÇÃO DO TÉCNICO: Forneça orientações diretas de como o gestor deve conduzir o técnico responsável, "
    "incluindo sempre uma mensagem pronta, assertiva e profissional para copiar e colar no chat interno."
)


class TicketAdvisorEngine:
    """Motor de inteligência analítica e consultoria para o Ticket Manager."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()
        self.gemini_client: Optional[genai.Client] = None
        if self.settings.gemini_api_key:
            try:
                self.gemini_client = genai.Client(api_key=self.settings.gemini_api_key)
            except Exception as e:
                console.print(f"[yellow]Aviso ao inicializar Gemini Client no TicketAdvisor: {e}[/yellow]")
        self.primary_model = self.settings.gemini_model or "gemini-3.5-flash-lite"

    def _executar_ia(self, system_instruction: str, user_prompt: str) -> str:
        """Executa a chamada à IA com cascata resiliente Gemini -> Gemini Fallback -> NVIDIA NIM."""
        # 1. Tentativa com modelos Gemini
        modelos_gemini = [self.primary_model]
        if "gemini-3.1-flash-lite" not in modelos_gemini:
            modelos_gemini.append("gemini-3.1-flash-lite")

        if self.gemini_client:
            for mod in modelos_gemini:
                try:
                    resp = self.gemini_client.models.generate_content(
                        model=mod,
                        contents=user_prompt,
                        config={"system_instruction": system_instruction},
                    )
                    if resp and resp.text:
                        return resp.text.strip()
                except Exception as e:
                    err_str = str(e)
                    console.print(
                        f"[dim yellow]Modelo Gemini '{mod}' indisponível ({err_str[:80]}...). Avançando cascata...[/dim yellow]"
                    )

        # 2. Fallback resiliente para NVIDIA NIM (Minimax m3.1-flash)
        nvidia_key = self.settings.nvidia_api_key
        if nvidia_key:
            try:
                console.print("[dim cyan]Ativando fallback automático NVIDIA NIM (Minimax m3.1-flash)...[/dim cyan]")
                url = "https://integrate.api.nvidia.com/v1/chat/completions"
                headers = {
                    "Authorization": f"Bearer {nvidia_key}",
                    "Content-Type": "application/json",
                }
                payload = {
                    "model": "minimaxai/minimax-m3.1-flash",
                    "messages": [
                        {"role": "system", "content": system_instruction},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0.2,
                    "max_tokens": 3000,
                }
                with httpx.Client(timeout=45.0) as http_client:
                    r = http_client.post(url, headers=headers, json=payload)
                    r.raise_for_status()
                    data = r.json()
                    out = data["choices"][0]["message"]["content"]
                    if out:
                        return out.strip()
            except Exception as nim_err:
                console.print(f"[bold red]Falha no fallback NVIDIA NIM:[/bold red] {nim_err}")

        return "Não foi possível gerar a análise no momento devido a indisponibilidade temporária dos provedores de IA."

    def analisar_chamado_profundo(
        self,
        dossie: dict[str, Any],
        pergunta_gestor: str = "",
        historico_conversa_chamado: Optional[list[dict[str, str]]] = None,
    ) -> str:
        """Realiza análise profunda e diagnóstica de um chamado com base no dossiê completo.

        Adapta dinamicamente entre Modo Operação (Chamado Aberto) e Modo QA (Chamado Finalizado).
        """
        chamado = dossie.get("chamado") or {}
        acompanhamentos = dossie.get("acompanhamentos") or []
        codigo = dossie.get("codigo") or chamado.get("codigo") or "N/D"

        # Detecta status de encerramento
        status_raw = str(chamado.get("status") or "").strip()
        status_lower = status_raw.lower()
        is_finalizado = (
            status_lower in ("finalizado", "resolvido", "fechado", "conferência", "conferencia", "cancelado")
            or bool(chamado.get("data_solucao") and str(chamado.get("data_solucao")).strip() not in ("", "Em aberto", "None", "null"))
        )

        avaliacao_val = chamado.get("total_avaliacao")
        desc_aval = chamado.get("descricao_avaliacao")
        aval_str = "Sem avaliação registrada"
        if avaliacao_val is not None and str(avaliacao_val).strip():
            aval_str = f"★ {avaliacao_val}/5 estrelas"
            if desc_aval:
                aval_str += f" - Comentário do cliente: \"{desc_aval}\""

        disp_vinc = chamado.get("dispositivo_vinculado")
        disp_str = "Não vinculado"
        if isinstance(disp_vinc, dict):
            disp_str = disp_vinc.get("hostname") or disp_vinc.get("patrimonio") or str(disp_vinc)
        elif disp_vinc:
            disp_str = str(disp_vinc)

        # Montagem do bloco de metadados ricos oficiais
        meta_bloco = [
            f"# DOSSIÊ OFICIAL DO CHAMADO #{codigo}",
            f"- **Cliente / Empresa:** {chamado.get('cliente') or 'Não informado'} (Token: {chamado.get('cliente_token') or 'N/D'})",
            f"- **Contato:** {chamado.get('contato') or 'Não informado'}" + (f" | Telefone: {chamado.get('telefone')}" if chamado.get('telefone') else "") + (f" | Setor: {chamado.get('setor')}" if chamado.get('setor') else ""),
            f"- **E-mail de Notificação/Conferência:** {chamado.get('email_conferencia') or 'N/D'}",
            f"- **Operador / Técnico Atribuído:** {chamado.get('tecnico') or 'Não atribuído'} (E-mail: {chamado.get('email_tecnico') or 'N/D'})",
            f"- **Mesa de Trabalho:** {chamado.get('mesa_trabalho') or 'Não informada'}",
            f"- **Status Oficial (API Milvus):** {status_raw or 'Não informado'}" + (f" (Motivo da Pausa: {chamado.get('motivo_pausa')})" if chamado.get('motivo_pausa') else ""),
            f"- **Prioridade:** {chamado.get('prioridade') or 'Não definida'}",
            f"- **Tipo de Ticket / Origem:** {chamado.get('tipo_ticket') or chamado.get('origem') or 'Suporte'}",
            f"- **SLA Solução:** {chamado.get('status_sla_solucao') or 'Sem SLA'} | **SLA Resposta:** {chamado.get('status_sla_resposta') or 'Sem SLA'}",
            f"- **Categorias:** Primária: {chamado.get('categoria_primaria') or 'N/D'} | Secundária: {chamado.get('categoria_secundaria') or 'N/D'}",
            f"- **Dispositivo Vinculado:** {disp_str}",
            f"- **Total de Horas Gastas:** {chamado.get('total_horas') or 'N/D'}",
            f"- **Avaliação do Cliente:** {aval_str}",
            f"- **Serviço Realizado / Solução Registrada:** {chamado.get('servico_realizado') or 'Nenhum resumo técnico cadastrado no fechamento'}",
            f"- **Datas Oficiais:** Criação: {chamado.get('data_criacao') or 'N/D'} | Modificação: {chamado.get('data_modificacao') or 'N/D'} | Solução: {chamado.get('data_solucao') or ('Finalizado' if is_finalizado else 'Em aberto')}",
            f"- **Assunto Oficial:** {chamado.get('assunto') or 'Sem assunto'}",
            f"\n### DESCRIÇÃO INICIAL DO CHAMADO:\n{chamado.get('descricao') or 'Sem descrição cadastrada.'}\n",
        ]

        # Campos extras caso disponíveis
        extras = []
        for c in ("impacto", "urgencia", "ultima_log"):
            if chamado.get(c):
                extras.append(f"- **{c.replace('_', ' ').capitalize()}:** {chamado.get(c)}")
        if extras:
            meta_bloco.append("### DADOS ADICIONAIS:\n" + "\n".join(extras) + "\n")

        # Montagem da Linha do Tempo de Acompanhamentos
        if acompanhamentos:
            acomp_bloco = [f"### HISTÓRICO DE ACOMPANHAMENTOS E COMENTÁRIOS ({len(acompanhamentos)} interações):"]
            for idx, a in enumerate(acompanhamentos, 1):
                visib = "[PRIVADO / INTERNO]" if a.get("privado") else "[PÚBLICO / VISÍVEL AO CLIENTE]"
                pessoa = a.get("pessoa") or "Operador/Sistema"
                perfil = a.get("perfil") or "Geral"
                dt = a.get("data") or "Data N/D"
                txt = a.get("texto") or "(Sem texto)"
                tipo = f" | Tipo: {a.get('tipo')}" if a.get("tipo") else ""
                acomp_bloco.append(f"--- Interação #{idx} ---")
                acomp_bloco.append(f"[{dt}] {pessoa} ({perfil}) {visib}{tipo}")
                acomp_bloco.append(f"{txt}\n")
            acomp_texto = "\n".join(acomp_bloco)
        else:
            acomp_texto = "### HISTÓRICO DE ACOMPANHAMENTOS:\nNenhum acompanhamento ou comentário registrado até o momento."

        # Histórico de perguntas anteriores na sessão (se houver)
        hist_sessao = ""
        if historico_conversa_chamado:
            hist_sessao = "\n### PERGUNTAS E RESPOSTAS ANTERIORES NESTA SESSÃO SOBRE ESTE CHAMADO:\n"
            for item in historico_conversa_chamado[-4:]:
                hist_sessao += f"- Pergunta: {item.get('pergunta', '')}\n- Resumo da Resposta: {item.get('resposta', '')[:200]}...\n"

        pergunta_final = pergunta_gestor.strip() if pergunta_gestor else (
            "Faça uma auditoria de qualidade pós-atendimento (QA) deste chamado finalizado."
            if is_finalizado
            else "Faça uma auditoria profunda deste chamado, diagnosticando a situação e orientando como devo operar o técnico."
        )

        if is_finalizado:
            instrucoes_modo = (
                "ATENÇÃO: ESTE CHAMADO ESTÁ FINALIZADO/RESOLVIDO. VOCÊ ESTÁ NO MODO AUDITORIA DE QUALIDADE PÓS-ATENDIMENTO (QA).\n"
                "REGRAS ESTREITAS DE AUDITORIA:\n"
                "- PRESERVAÇÃO DE DATAS: NUNCA altere os anos ou datas reais dos logs (ex: preserve estritamente o ano real informado nos logs e metadados, nunca troque por anos futuros).\n"
                "- FONTE DA VERDADE: O status oficial é Finalizado. NUNCA mencione inconsistência cadastral de status.\n"
                "- FOCO EM QA: O chamado já foi resolvido e encerrado! NÃO sugira troubleshooting, NÃO mande rodar comandos e NÃO sugira incomodar o cliente (a menos que haja evidência cabal de pendência aberta não resolvida no histórico).\n\n"
                "Siga rigorosamente as 5 seções abaixo:\n\n"
                "### 1. Raio-X do Encerramento & Desempenho\n"
                "- Status final oficial, tempo total gasto (total_horas), cumprimento do SLA de solução e operador/mesa responsáveis.\n"
                "- Avaliação do cliente em destaque (nota em estrelas ★ e comentário se houver).\n\n"
                "### 2. Linha do Tempo Real & Evidências\n"
                "- Resumo cronológico do que o cliente relatou e do que o técnico executou até o fechamento.\n"
                "- Reconheça e comente explicitamente as evidências visuais/prints identificados nos comentários (indicando o que foi demonstrado nelas, ex: AnyDesk, telas de erro, confirmação).\n\n"
                "### 3. Avaliação Técnica & Documental (QA)\n"
                "- Avalie se o problema foi adequadamente solucionado e a qualidade da documentação deixada pelo técnico no 'Serviço Realizado' e fechamento.\n"
                "- Se o trabalho foi ágil e bem avaliado pelo cliente (nota 5 estrelas), reconheça a excelência. Indique de forma construtiva se a documentação interna poderia ter mais detalhes para enriquecer a base de conhecimento.\n\n"
                "### 4. Conformidade de Governança no Milvus\n"
                "- Avalie o preenchimento de Categoria Primária/Secundária, Prioridade, Dispositivo Vinculado (hostname), Tipo de Ticket e Operador.\n\n"
                "### 5. Feedback do Ticket Manager ao Técnico\n"
                "- Parecer executivo reconhecendo a boa atuação/agilidade/nota 5 do técnico e apontando melhorias documentais se houver.\n"
                "- Bloco delimitado pronto para copiar e colar para o operador elogiando a entrega e alinhando eventuais pontos."
            )
        else:
            instrucoes_modo = (
                "ATENÇÃO: ESTE CHAMADO ESTÁ ABERTO/EM ANDAMENTO. VOCÊ ESTÁ NO MODO OPERAÇÃO & DESTRAVAMENTO DE SLA.\n"
                "REGRAS ESTREITAS:\n"
                "- PRESERVAÇÃO DE DATAS: NUNCA altere os anos ou datas reais dos logs (preserve estritamente os anos reais informados).\n"
                "- FOCO EM RESOLUÇÃO: O chamado está pendente e precisa ser resolvido dentro do SLA!\n\n"
                "Siga rigorosamente as 5 seções abaixo:\n\n"
                "### 1. Raio-X & Situação Real\n"
                "- Estado atual objetivo, tempo total aberto, conformidade do SLA e diagnóstico de gargalos/silêncio prolongado.\n\n"
                "### 2. O Que Já Foi Feito (Linha do Tempo)\n"
                "- Resumo cronológico objetivo do que o cliente relatou e do que o técnico e cliente executaram até agora, citando prints/evidências visuais.\n\n"
                "### 3. Diagnóstico Técnico & Próximos Passos de Resolução\n"
                "- Diagnóstico da provável causa raiz e plano de ação técnico exato com procedimentos e comandos recomendados (PowerShell, rede, DNS, etc.) para resolver agora.\n\n"
                "### 4. Modificações Recomendadas no Chamado\n"
                "- Sugestões cirúrgicas de governança no Milvus (ajustar Prioridade, Categoria, Mesa, Pausa indevida, vincular Dispositivo).\n\n"
                "### 5. Como Operar o Técnico (Plano de Gestão + Mensagem Pronta)\n"
                "- Orientação prática de gestão para o Ticket Manager cobrar e direcionar o técnico responsável para fechar dentro do SLA.\n"
                "- Bloco delimitado pronto para copiar e colar no chat interno orientando o operador."
            )

        prompt_usuario = (
            f"{chr(10).join(meta_bloco)}\n"
            f"{acomp_texto}\n"
            f"{hist_sessao}\n"
            f"### SOLICITAÇÃO / PERGUNTA DO TICKET MANAGER:\n{pergunta_final}\n\n"
            f"{instrucoes_modo}\n\n"
            "(Nota: se a solicitação do gestor for uma pergunta pontual de continuação, como 'Gere uma mensagem mais firme' ou 'Qual comando PowerShell testar?', "
            "responda diretamente com máxima assertividade técnica mantendo o contexto do chamado)."
        )

        return self._executar_ia(SYSTEM_INSTRUCTION_TICKET_ADVISOR, prompt_usuario)

    def analisar_carteira_operador(
        self,
        nome_operador: str,
        chamados_df: pd.DataFrame,
        pergunta_gestor: str = "",
    ) -> str:
        """Analisa a carteira completa de chamados abertos de um operador no DuckDB local.

        Gera um Plano Tático de Condução do Técnico com ordem exata de ataque e gestão de risco.
        """
        if chamados_df.empty:
            return (
                f"### Carteira do Operador: {nome_operador}\n\n"
                f"✓ O operador **{nome_operador}** não possui chamados abertos registrados no cache local neste momento.\n"
                f"Todos os chamados atribuídos a ele constam como concluídos ou a base local precisa de sincronização (`/sincronizar`)."
            )

        df = chamados_df.copy()
        total_chamados = len(df)

        # Trata campos de status e SLA
        if "status_sla_solucao" in df.columns:
            df["sla_norm"] = df["status_sla_solucao"].apply(normalizar_sla_texto)
        else:
            df["sla_norm"] = "Sem SLA"

        if "prioridade" in df.columns:
            df["prioridade_norm"] = df["prioridade"].apply(normalizar_prioridade_texto)
        else:
            df["prioridade_norm"] = "Não definida"

        # Contagens agregadas
        status_counts = df["status"].value_counts().to_dict() if "status" in df.columns else {}
        sla_counts = df["sla_norm"].value_counts().to_dict()
        prio_counts = df["prioridade_norm"].value_counts().to_dict()

        # Montagem de tabela resumida dos chamados
        linhas_tabela = []
        for _, row in df.iterrows():
            cod = str(row.get("codigo") or row.get("id") or "N/D")
            cli = str(row.get("cliente") or "N/D")[:22]
            assunto = str(row.get("assunto") or "Sem assunto")[:35]
            st = str(row.get("status") or "N/D")
            sla = str(row.get("sla_norm") or "Sem SLA")
            prio = str(row.get("prioridade_norm") or "N/D")
            dt = str(row.get("data_criacao") or "N/D")[:10]
            linhas_tabela.append(f"| #{cod} | {cli} | {assunto} | {st} | {prio} | {sla} | {dt} |")

        tabela_md = (
            "| Código | Cliente | Assunto | Status | Prioridade | SLA Solução | Criado em |\n"
            "| :--- | :--- | :--- | :--- | :--- | :--- | :--- |\n" + "\n".join(linhas_tabela)
        )

        pergunta_final = pergunta_gestor.strip() if pergunta_gestor else f"Como devo conduzir e operar o técnico {nome_operador} hoje?"

        prompt_usuario = (
            f"# CARTEIRA DE CHAMADOS DO OPERADOR: {nome_operador.upper()}\n\n"
            f"- **Total de Chamados Abertos:** {total_chamados}\n"
            f"- **Distribuição por Status:** {json.dumps(status_counts, ensure_ascii=False)}\n"
            f"- **Distribuição por SLA Solução:** {json.dumps(sla_counts, ensure_ascii=False)}\n"
            f"- **Distribuição por Prioridade:** {json.dumps(prio_counts, ensure_ascii=False)}\n\n"
            f"### TABELA DE CHAMADOS EM CARTEIRA:\n"
            f"{tabela_md}\n\n"
            f"### PERGUNTA DO TICKET MANAGER:\n{pergunta_final}\n\n"
            "INSTRUÇÕES OBRIGATÓRIAS DE ESTRUTURAÇÃO (Plano Tático de Gestão):\n"
            "Responda em Markdown limpo, profissional e objetivo nas seguintes seções:\n"
            "### 1. Visão Geral da Carteira\n"
            "- Diagnóstico da carga de trabalho, volume de chamados em atendimento vs pausados e saúde dos SLAs.\n\n"
            "### 2. Fila de Risco & Alertas Críticos\n"
            "- Alerte chamados estourados ou prestes a estourar, chamados antigos ou pausados sem evolução.\n\n"
            "### 3. Ordem Exata de Ataque (Priorização Tática de Hoje)\n"
            "- Indique a sequência numérica recomendada de execução (1º, 2º, 3º...) informando #Código, Cliente e por que priorizar agora.\n\n"
            "### 4. Como Operar o Técnico (Roteiro de Alinhamento 1:1)\n"
            "- Pontos-chave de alinhamento para o Ticket Manager cobrar do técnico (destravar impasses, solicitar apoio ou reatribuir).\n\n"
            "### 5. Mensagem de Alinhamento Rápido\n"
            "- Bloco curto e pronto para copiar e colar para o técnico no chat interno (Teams/Slack/WhatsApp) cobrando a ordem de ataque do dia."
        )

        return self._executar_ia(SYSTEM_INSTRUCTION_TICKET_ADVISOR, prompt_usuario)
