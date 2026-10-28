"""Módulo de análise de dados cirúrgica (Pandas, SciPy, Matplotlib/Seaborn)."""

from anakin.analytics.surgical_metrics import AnalyticsParams, TicketAnalyticsEngine
from anakin.analytics.realtime_plotter import RealTimeDashboardPlotter
from anakin.analytics.ticket_advisor import TicketAdvisorEngine

__all__ = [
    "AnalyticsParams",
    "TicketAnalyticsEngine",
    "RealTimeDashboardPlotter",
    "TicketAdvisorEngine",
]
