"""Plotter em tempo real do painel operacional do A.N.A.K.I.N (Matplotlib + Seaborn).

Renderiza figura 2x2 com tema escuro profissional, salva snapshots em PNG
e mantém o GUI Event Loop ativo para evitar travamentos no Windows.
"""

from datetime import datetime
from pathlib import Path
from typing import Optional
import asyncio
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns

from anakin.analytics.surgical_metrics import TicketAnalyticsEngine

# Configura tema escuro profissional global do Seaborn / Matplotlib
plt.style.use("dark_background")
sns.set_theme(style="darkgrid", palette="dark")


class RealTimeDashboardPlotter:
    """Gerenciador do painel visual 2x2 em tempo real."""

    def __init__(self, output_png_path: str = "data/painel_tempo_real.png") -> None:
        self.output_png = Path(output_png_path)
        self.output_png.parent.mkdir(parents=True, exist_ok=True)
        self.fig: Optional[plt.Figure] = None
        self.axes: Optional[np.ndarray] = None
        plt.ion()  # Ativa modo interativo do Matplotlib

    def _obter_ou_criar_figura(self) -> tuple[plt.Figure, np.ndarray]:
        """Garante a existência da janela gráfica interativa 2x2."""
        if self.fig is None or not plt.fignum_exists(self.fig.number):
            self.fig, self.axes = plt.subplots(2, 2, figsize=(15, 9))
            self.fig.patch.set_facecolor("#0d1117")
            try:
                self.fig.canvas.manager.set_window_title("A.N.A.K.I.N - Monitor Operacional em Tempo Real")
            except Exception:
                pass
        else:
            for ax in self.axes.flat:
                ax.clear()
        return self.fig, self.axes

    def renderizar_painel(self, engine: TicketAnalyticsEngine) -> Path:
        """Desenha os 4 quadrantes analíticos e salva snapshot em disco."""
        fig, axes = self._obter_ou_criar_figura()
        ax_sla, ax_status_prio = axes[0, 0], axes[0, 1]
        ax_tecnicos, ax_clientes = axes[1, 0], axes[1, 1]

        # Ajusta cor de fundo de cada subplot
        for ax in axes.flat:
            ax.set_facecolor("#161b22")
            ax.tick_params(colors="#c9d1d9")
            ax.grid(color="#30363d", linestyle="--", linewidth=0.5, alpha=0.7)

        total_chamados = len(engine.df)
        horario_atual = datetime.now().strftime("%d/%m/%Y %H:%M:%S")

        fig.suptitle(
            f"A.N.A.K.I.N - Operação & Risco de SLAs (Milvus Helpdesk)\n"
            f"Total Analisado: {total_chamados} Chamados Abertos | Última Atualização: {horario_atual}",
            fontsize=13,
            fontweight="bold",
            color="#58a6ff",
            y=0.98,
        )

        # -------------------------------------------------------------
        # Quadrante 1 (Superior Esquerdo): Saúde do SLA
        # -------------------------------------------------------------
        df_sla = engine.metricas_sla()
        cores_sla_map = {
            "Em conformidade": "#2ecc71",
            "Prestes a estourar": "#f39c12",
            "Estourado": "#e74c3c",
            "Sem SLA": "#95a5a6",
        }

        if not df_sla.empty:
            cores = [cores_sla_map.get(cat, "#34495e") for cat in df_sla["status_sla"]]
            bars = ax_sla.barh(df_sla["status_sla"], df_sla["quantidade"], color=cores, edgecolor="#30363d", height=0.6)
            ax_sla.set_title("1. Saúde do SLA de Solução", fontsize=11, fontweight="bold", color="#3fb950", pad=8)
            ax_sla.set_xlabel("Quantidade de Chamados", fontsize=9, color="#8b949e")
            ax_sla.invert_yaxis()

            max_x = df_sla["quantidade"].max() if not df_sla.empty else 10
            ax_sla.set_xlim(0, max_x * 1.35)

            offset = max(0.2, max_x * 0.02)
            for bar, (_, row) in zip(bars, df_sla.iterrows()):
                largura = bar.get_width()
                texto = f"{int(largura)} ({row['percentual']}%)"
                ax_sla.text(largura + offset, bar.get_y() + bar.get_height() / 2, texto, va="center", ha="left", fontsize=9, color="#f0f6fc", fontweight="bold")
        else:
            ax_sla.text(0.5, 0.5, "Nenhum dado de SLA", ha="center", va="center", color="#8b949e")
            ax_sla.set_title("1. Saúde do SLA de Solução", fontsize=11, fontweight="bold", color="#3fb950")

        # -------------------------------------------------------------
        # Quadrante 2 (Superior Direito): Status vs Prioridade
        # -------------------------------------------------------------
        ct_prio = engine.crosstab_status_prioridade()
        cores_prio = {
            "Crítico": "#f85149",
            "Alto": "#d29922",
            "Médio": "#e3b341",
            "Baixo": "#58a6ff",
            "Planejado": "#bc8cff",
            "Não definida": "#8b949e",
        }

        if not ct_prio.empty:
            paleta_usada = [cores_prio.get(col, "#8b949e") for col in ct_prio.columns]
            ct_prio.plot(
                kind="bar",
                stacked=True,
                ax=ax_status_prio,
                color=paleta_usada,
                edgecolor="#30363d",
                rot=15,
                width=0.55,
            )
            ax_status_prio.set_title("2. Status x Prioridade (Carga)", fontsize=11, fontweight="bold", color="#d29922", pad=8)
            ax_status_prio.set_xlabel("Status", fontsize=9, color="#8b949e")
            ax_status_prio.set_ylabel("Chamados", fontsize=9, color="#8b949e")
            max_y = ct_prio.sum(axis=1).max() if not ct_prio.empty else 10
            ax_status_prio.set_ylim(0, max_y * 1.35)
            ax_status_prio.legend(title="Prioridade", fontsize=8, title_fontsize=8, facecolor="#161b22", edgecolor="#30363d", loc="upper right")
        else:
            ax_status_prio.text(0.5, 0.5, "Sem chamados para cruzamento", ha="center", va="center", color="#8b949e")
            ax_status_prio.set_title("2. Status x Prioridade (Carga)", fontsize=11, fontweight="bold", color="#d29922")

        # -------------------------------------------------------------
        # Quadrante 3 (Inferior Esquerdo): Top Técnicos (Carga & Risco)
        # -------------------------------------------------------------
        df_tec = engine.ranking_tecnicos(top_n=7)
        if not df_tec.empty:
            y_pos = np.arange(len(df_tec))
            bars_tot = ax_tecnicos.barh(y_pos, df_tec["total_chamados"], color="#1f6feb", edgecolor="#30363d", height=0.6, label="Total")
            bars_risco = ax_tecnicos.barh(y_pos, df_tec["em_risco"], color="#da3633", alpha=0.9, edgecolor="#30363d", height=0.6, label="Em Risco de SLA")

            ax_tecnicos.set_yticks(y_pos)
            nomes_truncados = [t[:22] + ".." if len(t) > 22 else t for t in df_tec["tecnico"]]
            ax_tecnicos.set_yticklabels(nomes_truncados, fontsize=9)
            ax_tecnicos.invert_yaxis()
            ax_tecnicos.set_title("3. Top Técnicos (Total vs SLA em Risco)", fontsize=11, fontweight="bold", color="#58a6ff", pad=8)
            ax_tecnicos.set_xlabel("Chamados Atribuídos", fontsize=9, color="#8b949e")
            ax_tecnicos.legend(fontsize=8, facecolor="#161b22", edgecolor="#30363d", loc="lower right")

            max_tec = df_tec["total_chamados"].max() if not df_tec.empty else 10
            ax_tecnicos.set_xlim(0, max_tec * 1.38)
            offset_tec = max(0.15, max_tec * 0.02)

            for idx, row in df_tec.iterrows():
                val = row["total_chamados"]
                risco = row["em_risco"]
                label_txt = f"{val} (Risco: {risco})" if risco > 0 else f"{val}"
                ax_tecnicos.text(val + offset_tec, idx, label_txt, va="center", ha="left", fontsize=8.5, color="#f0f6fc", fontweight="bold")
        else:
            ax_tecnicos.text(0.5, 0.5, "Nenhum técnico atribuído", ha="center", va="center", color="#8b949e")
            ax_tecnicos.set_title("3. Top Técnicos (Carga & Risco)", fontsize=11, fontweight="bold", color="#58a6ff")

        # -------------------------------------------------------------
        # Quadrante 4 (Inferior Direito): Top Clientes (Demanda & Outliers)
        # -------------------------------------------------------------
        df_cli = engine.ranking_clientes_outliers(top_n=7)
        if not df_cli.empty:
            y_pos_cli = np.arange(len(df_cli))
            nomes_formatados = []
            cores_cli = []
            for _, row in df_cli.iterrows():
                nome = str(row["cliente"])
                trunc = nome[:22] + ".." if len(nome) > 22 else nome
                if row["is_outlier"]:
                    nomes_formatados.append(f"{trunc} * [z={row['z_score']}]")
                    cores_cli.append("#f85149")  # Vermelho para outliers
                else:
                    nomes_formatados.append(trunc)
                    cores_cli.append("#8957e5")  # Roxo padrão

            bars_cli = ax_clientes.barh(y_pos_cli, df_cli["total_chamados"], color=cores_cli, edgecolor="#30363d", height=0.6)
            ax_clientes.set_yticks(y_pos_cli)
            ax_clientes.set_yticklabels(nomes_formatados, fontsize=8.5)
            ax_clientes.invert_yaxis()
            ax_clientes.set_title("4. Top Clientes (* = Outlier Z-Score >= 1.8)", fontsize=11, fontweight="bold", color="#bc8cff", pad=8)
            ax_clientes.set_xlabel("Chamados Abertos", fontsize=9, color="#8b949e")

            max_cli = df_cli["total_chamados"].max() if not df_cli.empty else 10
            ax_clientes.set_xlim(0, max_cli * 1.35)
            offset_cli = max(0.15, max_cli * 0.02)

            for idx, row in df_cli.iterrows():
                val = row["total_chamados"]
                ax_clientes.text(val + offset_cli, idx, f"{val}", va="center", ha="left", fontsize=9, color="#f0f6fc", fontweight="bold")
        else:
            ax_clientes.text(0.5, 0.5, "Nenhum cliente identificado", ha="center", va="center", color="#8b949e")
            ax_clientes.set_title("4. Top Clientes (Demanda)", fontsize=11, fontweight="bold", color="#bc8cff")

        # Ajuste fino de layout
        fig.tight_layout(rect=[0, 0.02, 1, 0.94])

        # Salva o snapshot em disco com alta fidelidade
        fig.savefig(str(self.output_png), dpi=130, bbox_inches="tight", facecolor=fig.get_facecolor(), edgecolor="none")

        # Notifica e processa eventos da janela
        try:
            fig.canvas.draw_idle()
            fig.canvas.flush_events()
            plt.pause(0.001)
        except Exception:
            pass

        return self.output_png

    async def aguardar_com_gui_ativa(self, segundos: int) -> bool:
        """Aguarda mantendo o GUI loop ativo para responsividade no Windows.

        Retorna True se esperou todo o tempo, ou False se o usuário fechou a janela no 'X'.
        """
        if self.fig is None:
            await asyncio.sleep(segundos)
            return True

        passos = max(1, int(segundos / 0.2))
        for _ in range(passos):
            # Se o usuário fechou a janela gráfica
            if not plt.fignum_exists(self.fig.number):
                return False

            try:
                self.fig.canvas.flush_events()
                plt.pause(0.04)
            except Exception:
                return False

            await asyncio.sleep(0.16)

        return True

    def fechar(self) -> None:
        """Fecha a janela gráfica interativa."""
        if self.fig is not None and plt.fignum_exists(self.fig.number):
            plt.close(self.fig)
