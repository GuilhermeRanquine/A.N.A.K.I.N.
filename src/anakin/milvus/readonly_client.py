"""Cliente HTTP assíncrono para a API do Milvus com trava estrita de Somente Leitura (Read-Only).

Garante que nenhuma operação de escrita, criação, atualização ou exclusão ocorra,
respeitando o rate limit estrito de 1 requisição a cada 62 segundos.
"""

from typing import Any, Optional
import asyncio
import time
import re
import httpx
from aiolimiter import AsyncLimiter
from rich.console import Console

import sys
from anakin.config.settings import Settings, get_settings
from anakin.domain.ticket_models import TicketSummary, TicketFollowUp, _clean_text

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

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
        """Executa a requisição após validação de segurança e respeito ao rate limit com contagem visual."""
        self._validar_rota(method, endpoint)

        # Checagem visual do Rate Limit antes de aguardar
        now = time.monotonic()
        if self._last_request_time > 0:
            elapsed = now - self._last_request_time
            restante = self.settings.milvus_rate_limit_seconds - elapsed
            if restante > 0:
                console.print(
                    f"\n[bold yellow][Milvus Rate Limit][/bold yellow] Aguardando janela de segurança "
                    f"({restante:.1f}s restantes antes de liberar a requisição)..."
                )
                for r in range(int(restante), 0, -1):
                    console.print(f" [dim]Liberando requisição em {r:02d}s...[/dim]", end="\r")
                    await asyncio.sleep(1)
                console.print(" [dim green][OK] Janela de rate limit liberada! Executando requisição...[/dim green]                       ")

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

    async def obter_dossie_completo_chamado(
        self,
        codigo: str | int,
        forcar_api: bool = False,
        cache: Optional[Any] = None,
        max_cache_minutos: int = 10,
    ) -> dict[str, Any]:
        """Obtém o dossiê profundo e enriquecido de um chamado para o Copiloto Ticket Manager.

        1. Limpa o '#' caso venha '#69118' -> '69118'.
        2. Carrega acompanhamentos recentes do cache ou consulta GET /chamado/acompanhamento/{codigo}.
           - Processa 'texto_html', contando tags <img e anotando [EVIDÊNCIA VISUAL: contém X imagem(ns)/print(s) anexado(s) neste registro].
           - Categoriza por tipo/log_tipo_text: log de sistema vs nota interna vs comentário público.
        3. Verifica a frescura dos metadados:
           - Se forcar_api=True OU não existir no DuckDB OU a última consulta direta foi há mais de 5 minutos
             OU a data do último acompanhamento for mais recente que 'data_modificacao' salva no DuckDB:
             Consulta POST /chamado/listagem com status=10 (Todos) e salva os metadados frescos e raw_json
             com consulta_direta=True (atualizando 'ultima_consulta_direta_em').
        """
        import datetime
        from anakin.storage.local_cache import LocalCache

        local_cache = cache or LocalCache(settings=self.settings)

        codigo_limpo = str(codigo).lstrip("#").strip()
        origem_cache_chamado = False
        origem_cache_acompanhamentos = False

        # 1. Acompanhamentos no DuckDB cache ou API
        acompanhamentos_lista: Optional[list[dict[str, Any]]] = None
        if not forcar_api:
            acompanhamentos_lista = local_cache.obter_acompanhamentos_cache(
                codigo_limpo, max_idade_minutos=max_cache_minutos
            )
            if acompanhamentos_lista is not None:
                origem_cache_acompanhamentos = True

        if acompanhamentos_lista is None:
            console.print(
                f"[dim cyan]Consultando histórico e comentários do chamado #{codigo_limpo} na API Milvus...[/dim cyan]"
            )
            resp = await self._executar_requisicao(
                method="GET",
                endpoint=f"/chamado/acompanhamento/{codigo_limpo}",
            )
            data = resp.json()
            raw_items: list[dict[str, Any]] = []
            if isinstance(data, list):
                raw_items = data
            elif isinstance(data, dict):
                for k in ("retorno", "lista", "data", "acompanhamentos", "comentarios"):
                    if k in data and isinstance(data[k], list):
                        raw_items = data[k]
                        break

            acompanhamentos_filtrados: list[dict[str, Any]] = []
            for item in raw_items:
                if not isinstance(item, dict):
                    continue
                is_exc = item.get("is_excluido") or item.get("excluido") or item.get("deleted")
                if is_exc is True or str(is_exc).lower() in ("true", "1"):
                    continue

                norm = TicketFollowUp.model_validate(item)

                # Processamento de evidências visuais no texto_html
                texto_html = item.get("texto_html") or ""
                imgs_count = len(re.findall(r"<img\b", texto_html, re.IGNORECASE))
                evidencia_img = (
                    f" [EVIDÊNCIA VISUAL: contém {imgs_count} imagem(ns)/print(s) anexado(s) neste registro]"
                    if imgs_count > 0
                    else ""
                )

                # Classificação do tipo de registro
                tipo_val = str(item.get("tipo") or norm.tipo or "")
                log_tipo_text = str(item.get("log_tipo_text") or "").strip()
                is_privado = bool(item.get("privado") or item.get("is_privado") or item.get("interno"))

                if log_tipo_text:
                    rotulo_tipo = f"[LOG AUTOMÁTICO DE SISTEMA: {log_tipo_text}]"
                elif tipo_val in ("1", "sistema", "log"):
                    rotulo_tipo = "[LOG AUTOMÁTICO DE SISTEMA]"
                elif is_privado or tipo_val in ("2", "interno"):
                    rotulo_tipo = "[NOTA INTERNA PRIVADA]"
                else:
                    rotulo_tipo = "[COMENTÁRIO PÚBLICO AO CLIENTE]"

                texto_puro = norm.texto_limpo
                if not texto_puro and texto_html:
                    texto_puro = _clean_text(texto_html)

                texto_final = f"{rotulo_tipo}{evidencia_img} {texto_puro}".strip()

                d_item = {
                    "pessoa": norm.pessoa or item.get("pessoa") or item.get("usuario") or "Sistema/Operador",
                    "perfil": norm.perfil or item.get("perfil") or item.get("tipo_usuario") or "Operador",
                    "texto": texto_final,
                    "texto_puro": texto_puro,
                    "texto_html": texto_html,
                    "evidencias_visuais_count": imgs_count,
                    "rotulo_tipo": rotulo_tipo,
                    "data": norm.data or item.get("data") or item.get("data_hora") or "",
                    "privado": is_privado,
                    "tipo": tipo_val,
                    "log_tipo_text": log_tipo_text,
                }
                acompanhamentos_filtrados.append(d_item)

            local_cache.salvar_acompanhamentos_cache(codigo_limpo, acompanhamentos_filtrados)
            acompanhamentos_lista = acompanhamentos_filtrados

        # Identifica a data do último acompanhamento registrado
        ultima_data_acomp = ""
        if acompanhamentos_lista:
            datas = [str(a.get("data") or "") for a in acompanhamentos_lista if a.get("data")]
            if datas:
                ultima_data_acomp = max(datas)

        # 2. Metadados do chamado: controle rigoroso de frescor
        chamado_dados = local_cache.obter_chamado_por_codigo(codigo_limpo)
        precisa_atualizar_metadados = False

        if forcar_api or not chamado_dados:
            precisa_atualizar_metadados = True
        else:
            # Checagem 1: Janela de 5 minutos (300 segundos) de última consulta direta
            ucd = chamado_dados.get("ultima_consulta_direta_em")
            if ucd is None:
                precisa_atualizar_metadados = True
            else:
                agora = datetime.datetime.now()
                if hasattr(ucd, "tzinfo") and ucd.tzinfo is not None:
                    agora = datetime.datetime.now(datetime.timezone.utc)
                try:
                    diff_seg = (agora - ucd).total_seconds()
                    if diff_seg > 300:  # mais de 5 minutos
                        precisa_atualizar_metadados = True
                except Exception:
                    precisa_atualizar_metadados = True

            # Checagem 2: Se há acompanhamento posterior à data_modificacao salva no cache
            if not precisa_atualizar_metadados and ultima_data_acomp:
                data_mod = str(chamado_dados.get("data_modificacao") or "")
                if data_mod and ultima_data_acomp > data_mod:
                    precisa_atualizar_metadados = True

        if precisa_atualizar_metadados:
            console.print(
                f"[dim cyan]Atualizando metadados frescos do chamado #{codigo_limpo} na API Milvus (status=10 [Todos])...[/dim cyan]"
            )
            tickets_api = await self.listar_chamados(
                status=10,
                codigo=codigo_limpo,
                total_registros=5,
            )
            if tickets_api:
                local_cache.salvar_chamados(tickets_api, consulta_direta=True)
                chamado_dados = local_cache.obter_chamado_por_codigo(codigo_limpo)
                origem_cache_chamado = False
            else:
                console.print(f"[yellow]Aviso: Chamado #{codigo_limpo} não retornado na busca direta da API Milvus.[/yellow]")
        else:
            origem_cache_chamado = True

        return {
            "codigo": codigo_limpo,
            "chamado": chamado_dados or {"codigo": codigo_limpo, "assunto": f"Chamado #{codigo_limpo}"},
            "acompanhamentos": acompanhamentos_lista or [],
            "total_acompanhamentos": len(acompanhamentos_lista or []),
            "origem_cache_chamado": origem_cache_chamado,
            "origem_cache_acompanhamentos": origem_cache_acompanhamentos,
        }

    async def close(self) -> None:
        """Fecha o cliente HTTP se estiver aberto."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    async def __aenter__(self) -> "MilvusReadOnlyClient":
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()
