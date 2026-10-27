"""Cliente HTTP assíncrono para a API do Milvus com trava estrita de Somente Leitura (Read-Only).

Garante que nenhuma operação de escrita, criação, atualização ou exclusão ocorra,
respeitando o rate limit estrito de 1 requisição a cada 62 segundos.
"""

from typing import Any
import time
import httpx
from aiolimiter import AsyncLimiter
from rich.console import Console

from anakin.config.settings import Settings, get_settings
from anakin.domain.ticket_models import TicketSummary, TicketFollowUp

console = Console()


class ReadOnlySafetyError(Exception):
    """Exceção levantada ao tentar executar qualquer operação de mutação ou endpoint não autorizado."""


class MilvusReadOnlyClient:
    """Cliente Milvus estritamente seguro para consultas em produção.

    Allowlist rígida de endpoints permitidos:
      - POST /chamado/listagem (leitura com paginação e filtro)
      - GET /chamado/acompanhamento/{chamado_codigo} (leitura de histórico/comentários)
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.base_url = self.settings.milvus_base_url.rstrip("/")
        # Rate limit: 1 requisição para cada janela de milvus_rate_limit_seconds
        self._limiter = AsyncLimiter(1, self.settings.milvus_rate_limit_seconds)
        self._last_request_time: float = 0.0
        self._client: httpx.AsyncClient | None = None

    def _get_headers(self) -> dict[str, str]:
        """Retorna os cabeçalhos oficiais.

        IMPORTANTE: O Milvus exige o token PURO no header Authorization (sem prefixo Bearer).
        """
        return {
            "Authorization": self.settings.milvus_api_token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _validar_rota(self, method: str, path: str) -> None:
        """Verifica se a rota e método constam estritamente na Allowlist Read-Only."""
        if not self.settings.milvus_read_only:
            # Mesmo se desativado na config, a arquitetura deste cliente permanece restrita
            pass

        method_upper = method.upper()
        clean_path = path.strip()
        if not clean_path.startswith("/"):
            clean_path = "/" + clean_path

        # Regra 1: POST /chamado/listagem
        if method_upper == "POST" and clean_path == "/chamado/listagem":
            return

        # Regra 2: GET /chamado/acompanhamento/{chamado_codigo}
        if method_upper == "GET" and clean_path.startswith("/chamado/acompanhamento/"):
            return

        raise ReadOnlySafetyError(
            f"VIOLAÇÃO DE SEGURANÇA READ-ONLY: O método '{method_upper}' ou endpoint '{clean_path}' "
            f"NÃO é permitido. Este cliente opera exclusivamente em modo leitura estrita "
            f"(permitidos apenas: POST /chamado/listagem e GET /chamado/acompanhamento/)."
        )

    async def _obter_client(self) -> httpx.AsyncClient:
        """Inicializa ou reaproveita o cliente HTTP assíncrono."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=self._get_headers(),
                timeout=httpx.Timeout(connect=15.0, read=45.0, write=15.0, pool=15.0),
            )
        return self._client

    async def _executar_requisicao(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """Executa a requisição após validação de segurança e respeito ao rate limit."""
        self._validar_rota(method, endpoint)

        # Checagem visual do Rate Limit antes de aguardar
        now = time.monotonic()
        if self._last_request_time > 0:
            elapsed = now - self._last_request_time
            restante = self.settings.milvus_rate_limit_seconds - elapsed
            if restante > 0:
                console.print(
                    f"[bold yellow][Milvus Rate Limit][/bold yellow] Aguardando janela de segurança "
                    f"({restante:.1f}s restantes antes de liberar a requisição)..."
                )

        # Bloqueia até cumprir o intervalo exigido
        await self._limiter.acquire()
        self._last_request_time = time.monotonic()

        client = await self._obter_client()
        response = await client.request(
            method=method,
            url=endpoint,
            params=params,
            json=json_body,
        )
        response.raise_for_status()
        return response

    async def listar_chamados(
        self,
        status: int | str | None = 9,
        total_registros: int = 20,
        pagina: int = 1,
        codigo: str | None = None,
    ) -> list[TicketSummary]:
        """Consulta chamados na rota oficial de leitura POST /chamado/listagem.

        Args:
            status: Status do chamado (9 = Chamados Abertos por padrão). Se None, não filtra por status.
            total_registros: Quantidade de registros por página (query param).
            pagina: Número da página (query param).
            codigo: Código específico de chamado para busca direta (dentro do filtro_body).
        """
        params = {
            "total_registros": total_registros,
            "pagina": pagina,
            "order_by": "data_criacao",
            "is_descending": "true",
        }

        filtro_body: dict[str, Any] = {}
        if status is not None:
            filtro_body["status"] = status
        if codigo is not None:
            filtro_body["codigo"] = codigo

        payload = {"filtro_body": filtro_body}

        resp = await self._executar_requisicao(
            method="POST",
            endpoint="/chamado/listagem",
            params=params,
            json_body=payload,
        )

        data = resp.json()
        raw_items: list[dict[str, Any]] = []

        if isinstance(data, list):
            raw_items = data
        elif isinstance(data, dict):
            # Procura chaves comuns de resposta na API do Milvus
            for key in ("lista", "data", "retorno", "result", "chamados"):
                if key in data and isinstance(data[key], list):
                    raw_items = data[key]
                    break
            else:
                # Caso a resposta seja um objeto contendo dicionário de chamados
                if "chamado" in data and isinstance(data["chamado"], dict):
                    raw_items = [data["chamado"]]

        tickets: list[TicketSummary] = []
        for item in raw_items:
            try:
                tickets.append(TicketSummary.model_validate(item))
            except Exception as e:
                console.print(f"[dim yellow]Aviso ao processar chamado: {e}[/dim yellow]")

        return tickets

    async def listar_comentarios(
        self,
        chamado_codigo: int | str,
        apenas_comentarios: bool = False,
    ) -> list[TicketFollowUp]:
        """Consulta o histórico de acompanhamentos e comentários de um chamado.

        Rota oficial: GET /chamado/acompanhamento/{chamado_codigo}

        Args:
            chamado_codigo: Código identificador do chamado no Milvus.
            apenas_comentarios: Se True, envia query param `tipo="comentarios"`.
        """
        endpoint = f"/chamado/acompanhamento/{chamado_codigo}"
        params: dict[str, Any] = {}
        if apenas_comentarios:
            params["tipo"] = "comentarios"

        resp = await self._executar_requisicao(
            method="GET",
            endpoint=endpoint,
            params=params or None,
        )

        data = resp.json()
        raw_items: list[dict[str, Any]] = []

        if isinstance(data, list):
            raw_items = data
        elif isinstance(data, dict):
            for key in ("lista", "data", "retorno", "acompanhamentos", "comentarios"):
                if key in data and isinstance(data[key], list):
                    raw_items = data[key]
                    break

        comments: list[TicketFollowUp] = []
        for item in raw_items:
            try:
                comments.append(TicketFollowUp.model_validate(item))
            except Exception as e:
                console.print(f"[dim yellow]Aviso ao processar comentário: {e}[/dim yellow]")

        return comments

    async def close(self) -> None:
        """Fecha o cliente HTTP se estiver aberto."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    async def __aenter__(self) -> "MilvusReadOnlyClient":
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()
