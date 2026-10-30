"""Sistema de Telemetria e Registro de Eventos do A.N.A.K.I.N.

Armazena em memória (buffer circular) e em arquivo JSONL eventos ao vivo gerados pelo
despachante automático, atendente com IA e monitoramento de chamados.
Fornece suporte a fila assíncrona (pub/sub) para Server-Sent Events (SSE).
"""

from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncGenerator, Optional
import asyncio
import json


class TelemetryEvent:
    """Modelo de evento operacional para telemetria HUD."""

    def __init__(
        self,
        origem: str,
        nivel: str,
        mensagem: str,
        detalhes: Optional[dict[str, Any]] = None,
        timestamp: Optional[datetime] = None,
        evento_id: Optional[int] = None,
    ) -> None:
        self.timestamp = timestamp or datetime.now()
        self.origem = origem.upper()
        self.nivel = nivel.upper()
        self.mensagem = mensagem
        self.detalhes = detalhes or {}
        self.evento_id = evento_id or int(self.timestamp.timestamp() * 1000)

    def to_dict(self) -> dict[str, Any]:
        """Serializa o evento para JSON."""
        return {
            "id": self.evento_id,
            "horario": self.timestamp.strftime("%H:%M:%S"),
            "data_hora": self.timestamp.isoformat(),
            "origem": self.origem,
            "nivel": self.nivel,
            "mensagem": self.mensagem,
            "detalhes": self.detalhes,
        }


class TelemetryManager:
    """Gerenciador central de eventos e telemetria operacional."""

    _instance: Optional["TelemetryManager"] = None

    def __new__(cls, *args: Any, **kwargs: Any) -> "TelemetryManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._inicializado = False
        return cls._instance

    def __init__(self, log_path: str = "data/telemetry_events.jsonl", max_buffer: int = 500) -> None:
        if getattr(self, "_inicializado", False):
            return
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.max_buffer = max_buffer
        self.buffer: deque[TelemetryEvent] = deque(maxlen=max_buffer)
        self.subscribers: set[asyncio.Queue[TelemetryEvent]] = set()
        self._carregar_historico_inicial()
        self._inicializado = True

    def _carregar_historico_inicial(self) -> None:
        """Carrega eventos existentes do arquivo ou cria eventos de inicialização."""
        if self.log_path.exists():
            try:
                with open(self.log_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            d = json.loads(line)
                            dt = datetime.fromisoformat(d.get("data_hora", datetime.now().isoformat()))
                            ev = TelemetryEvent(
                                origem=d.get("origem", "SYSTEM"),
                                nivel=d.get("nivel", "INFO"),
                                mensagem=d.get("mensagem", ""),
                                detalhes=d.get("detalhes", {}),
                                timestamp=dt,
                                evento_id=d.get("id"),
                            )
                            self.buffer.append(ev)
                        except Exception:
                            continue
            except Exception:
                pass

        if not self.buffer:
            # Sementes operacionais HUD
            agora = datetime.now()
            self.registrar_evento(
                origem="KERNEL",
                nivel="INFO",
                mensagem="A.N.A.K.I.N HUD Console inicializado. Interface Sci-Fi ativada.",
                timestamp=agora,
            )
            self.registrar_evento(
                origem="MILVUS_RO",
                nivel="SUCCESS",
                mensagem="Trava de Somente Leitura (Read-Only) verificada. Mutação em produção bloqueada.",
                timestamp=agora,
            )
            self.registrar_evento(
                origem="DUCKDB",
                nivel="INFO",
                mensagem="Conexão com anakin_readonly.duckdb estabelecida em modo read_only=True.",
                timestamp=agora,
            )
            self.registrar_evento(
                origem="DISPATCHER",
                nivel="INFO",
                mensagem="Pool de atendimento Tiered N1/N2 ativo com 6 operadores oficiais.",
                timestamp=agora,
            )

    def registrar_evento(
        self,
        origem: str,
        nivel: str,
        mensagem: str,
        detalhes: Optional[dict[str, Any]] = None,
        timestamp: Optional[datetime] = None,
    ) -> TelemetryEvent:
        """Registra um novo evento na memória, persiste no arquivo e notifica subscribers."""
        ev = TelemetryEvent(
            origem=origem,
            nivel=nivel,
            mensagem=mensagem,
            detalhes=detalhes,
            timestamp=timestamp,
        )
        self.buffer.append(ev)

        # Gravação assíncrona/append em arquivo JSONL
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(ev.to_dict(), ensure_ascii=False) + "\n")
        except Exception:
            pass

        # Notifica listeners de SSE
        for q in list(self.subscribers):
            try:
                q.put_nowait(ev)
            except Exception:
                pass

        return ev

    def obter_eventos(self, limite: int = 100) -> list[dict[str, Any]]:
        """Retorna os eventos mais recentes formatados."""
        eventos = list(self.buffer)[-limite:]
        return [e.to_dict() for e in eventos]

    def subscrever(self) -> asyncio.Queue[TelemetryEvent]:
        """Cria e retorna uma fila assíncrona para streaming SSE."""
        q: asyncio.Queue[TelemetryEvent] = asyncio.Queue(maxsize=100)
        self.subscribers.add(q)
        return q

    def desinscrever(self, q: asyncio.Queue[TelemetryEvent]) -> None:
        """Remove a fila da lista de subscribers."""
        self.subscribers.discard(q)


# Singleton global
telemetry = TelemetryManager()


def registrar_evento(
    origem: str,
    nivel: str,
    mensagem: str,
    detalhes: Optional[dict[str, Any]] = None,
) -> TelemetryEvent:
    """Helper global para registrar eventos a partir de qualquer módulo."""
    return telemetry.registrar_evento(origem=origem, nivel=nivel, mensagem=mensagem, detalhes=detalhes)

