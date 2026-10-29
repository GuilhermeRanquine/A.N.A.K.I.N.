"""Cliente de criação controlada e segura de chamados no Milvus.

Garante trava estrita: permite exclusivamente consulta de clientes (GET /cliente/busca),
abertura de chamados (POST /chamado/criar) e envio opcional de anexo.
Proíbe qualquer método PUT, DELETE ou alteração em cadastros de clientes/contatos.
"""

from pathlib import Path
from typing import Any, Optional
import re
import time
import unicodedata
import duckdb
import httpx
from aiolimiter import AsyncLimiter
from rich.console import Console

from anakin.config.settings import Settings, get_settings
from anakin.domain.ticket_creation_models import MilvusCreateTicketPayload

console = Console()


PRIORIDADE_ID_MAP: dict[str, str] = {
    "baixo": "1",
    "baixa": "1",
    "médio": "2",
    "medio": "2",
    "média": "2",
    "media": "2",
    "alto": "3",
    "alta": "3",
    "crítico": "4",
    "critico": "4",
    "crítica": "4",
    "critica": "4",
}

MAPA_EMAIL_PARA_NOME_OPERADOR: dict[str, str] = {
    "carlos.bucazio@upgradeti.com.br": "Carlos Eduardo",
    "gabriel@upgradeti.com.br": "Gabriel Oliveira",
    "gabryel.santos@upgradeti.com.br": "Gabryel Santos",
    "gabriel.sena@upgradeti.com.br": "Gabriel Sena",
    "guilherme.melo@upgradeti.com.br": "Guilherme Maganha",
    "kaio.silva@upgradeti.com.br": "Kaio Silva",
}


def resolver_prioridade_id(prioridade_val: str | int | None) -> str:
    """Garante que a prioridade seja convertida para ID numérico string oficial ('1', '2', '3' ou '4')."""
    if not prioridade_val:
        return "2"
    val_str = str(prioridade_val).strip().lower()
    if val_str in ("1", "2", "3", "4"):
        return val_str
    return PRIORIDADE_ID_MAP.get(val_str, "2")


def normalizar_para_busca(texto: str) -> str:
    """Normaliza texto para busca removendo acentos, pontuação, traços e espaços."""
    if not texto:
        return ""
    nfkd = unicodedata.normalize("NFKD", str(texto))
    sem_acento = "".join([c for c in nfkd if not unicodedata.combining(c)])
    return re.sub(r"[^a-z0-9]", "", sem_acento.lower())


class CreationSafetyError(Exception):
    """Exceção levantada ao tentar violar a política estrita de criação de chamados."""


class MilvusTicketCreatorClient:
    """Cliente responsável pela busca de empresas e criação segura de chamados no Milvus."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.base_url = self.settings.milvus_base_url.rstrip("/")
        # Rate limit rigoroso de 1 requisição a cada 62s
        self._limiter = AsyncLimiter(1, self.settings.milvus_rate_limit_seconds)
        self._last_request_time: float = 0.0
        self._client: httpx.AsyncClient | None = None

    def _get_headers(self) -> dict[str, str]:
        """Retorna cabeçalhos oficiais com token puro (sem prefixo Bearer)."""
        return {
            "Authorization": self.settings.milvus_api_token,
            "Accept": "application/json",
        }

    def _validar_rota(self, method: str, path: str) -> None:
        """Trava rígida: Allowlist restrita a buscas e criação controlada."""
        method_upper = method.upper()
        clean_path = path.strip()
        if not clean_path.startswith("/"):
            clean_path = "/" + clean_path

        # Rota 1: GET /cliente/busca
        if method_upper == "GET" and clean_path == "/cliente/busca":
            return

        # Rota 2: POST /chamado/criar
        if method_upper == "POST" and clean_path == "/chamado/criar":
            return

        # Rota 3: POST /chamado/anexo/criar/{chamado_id}
        if method_upper == "POST" and clean_path.startswith("/chamado/anexo/criar/"):
            return

        # Rota 4: POST /chamado/listagem (para recuperar o id interno do chamado recém-criado)
        if method_upper == "POST" and clean_path == "/chamado/listagem":
            return

        # Rota 5: POST /chamado/atualizar (para atribuição controlada de técnico/operador)
        if method_upper == "POST" and clean_path == "/chamado/atualizar":
            return

        raise CreationSafetyError(
            f"VIOLAÇÃO DE SEGURANÇA: O método '{method_upper}' ou rota '{clean_path}' "
            f"não é permitido neste cliente. Proibida qualquer alteração em clientes ou rotas não autorizadas."
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

    async def _aguardar_rate_limit(self) -> None:
        """Exibe aviso e aguarda a janela de rate limit de 62s."""
        now = time.monotonic()
        if self._last_request_time > 0:
            elapsed = now - self._last_request_time
            restante = self.settings.milvus_rate_limit_seconds - elapsed
            if restante > 0:
                console.print(
                    f"[bold yellow][Milvus Rate Limit][/bold yellow] Aguardando janela de segurança "
                    f"({restante:.1f}s restantes antes da próxima requisição)..."
                )

        await self._limiter.acquire()
        self._last_request_time = time.monotonic()

    async def resolver_empresa(
        self,
        nome_busca: str,
        duckdb_path: str = "data/anakin_readonly.duckdb",
    ) -> list[dict[str, str]]:
        """Localiza empresas candidatas e seus respectivos tokens no Milvus.

        Estratégia eficiente e tolerante a espaços/acentos:
        1. Primeiro busca no DuckDB local (sem consumir requisição na API).
        2. Normaliza ambos os lados removendo espaços, traços e acentos (ex: 'upgradeti' -> 'UPGRADE TI').
        3. Se não encontrar localmente, consulta GET /cliente/busca na API oficial.
        """
        termo = nome_busca.strip()
        if not termo:
            return []

        termo_norm = normalizar_para_busca(termo)

        # 1. Busca no cache DuckDB local com normalização ampla
        db_file = Path(duckdb_path)
        if db_file.exists():
            try:
                with duckdb.connect(str(db_file), read_only=True) as con:
                    query = """
                        SELECT DISTINCT cliente, cliente_token
                        FROM chamados
                        WHERE cliente_token IS NOT NULL
                    """
                    rows = con.execute(query).fetchall()
                    candidatos_locais = []
                    ja_inseridos = set()

                    for r_cliente, r_token in rows:
                        if not r_cliente or not r_token:
                            continue
                        nome_str = str(r_cliente).strip()
                        token_str = str(r_token).strip()
                        cliente_norm = normalizar_para_busca(nome_str)

                        if termo_norm in cliente_norm or cliente_norm in termo_norm:
                            chave = (nome_str, token_str)
                            if chave not in ja_inseridos:
                                ja_inseridos.add(chave)
                                candidatos_locais.append({"nome": nome_str, "token": token_str})

                    if candidatos_locais:
                        console.print(f"[dim green]✓ Empresa localizada no cache local DuckDB (0 requisições gastas).[/dim green]")
                        return candidatos_locais
            except Exception as e:
                console.print(f"[dim yellow]Aviso ao consultar DuckDB local: {e}[/dim yellow]")

        # 2. Busca na API do Milvus: GET /cliente/busca
        self._validar_rota("GET", "/cliente/busca")
        await self._aguardar_rate_limit()

        client = await self._obter_client()
        resp = await client.get(
            "/cliente/busca",
            params={"nome_fantasia": termo, "status": 1},
        )
        resp.raise_for_status()

        data = resp.json()
        lista_clientes = data.get("lista", []) if isinstance(data, dict) else data

        # Se a busca exata vier vazia, consulta lista de clientes ativos e filtra com normalização
        if not lista_clientes:
            console.print(f"[dim]Buscando listagem geral de clientes ativos para correspondência ampla...[/dim]")
            await self._aguardar_rate_limit()
            resp_geral = await client.get(
                "/cliente/busca",
                params={"status": 1, "total_registros": 100},
            )
            resp_geral.raise_for_status()
            data_geral = resp_geral.json()
            lista_clientes = data_geral.get("lista", []) if isinstance(data_geral, dict) else data_geral

        candidatos: list[dict[str, str]] = []
        ja_inseridos_api = set()

        for c in lista_clientes:
            if not isinstance(c, dict):
                continue
            nome_fantasia = str(c.get("nome_fantasia") or "").strip()
            razao_social = str(c.get("razao_social") or "").strip()
            token = str(c.get("token") or "").strip()

            if not token:
                continue

            nf_norm = normalizar_para_busca(nome_fantasia)
            rz_norm = normalizar_para_busca(razao_social)

            # Checa correspondência com o termo normalizado
            if (
                (nf_norm and (termo_norm in nf_norm or nf_norm in termo_norm))
                or (rz_norm and (termo_norm in rz_norm or rz_norm in termo_norm))
            ):
                nome_exibicao = nome_fantasia if nome_fantasia else razao_social
                chave = (nome_exibicao, token)
                if chave not in ja_inseridos_api:
                    ja_inseridos_api.add(chave)
                    candidatos.append({"nome": nome_exibicao, "token": token})

        return candidatos

    def resolver_tecnico_local(
        self,
        termo_tecnico: str,
        duckdb_path: str = "data/anakin_readonly.duckdb",
    ) -> Optional[str]:
        """Localiza o nome oficial do operador no DuckDB local."""
        if not termo_tecnico:
            return None
        db_file = Path(duckdb_path)
        if not db_file.exists():
            return None
        try:
            termo_norm = normalizar_para_busca(termo_tecnico)
            with duckdb.connect(str(db_file), read_only=True) as con:
                rows = con.execute("SELECT nome_exibicao, email_tecnico FROM operadores_oficiais").fetchall()
                for nome, email in rows:
                    if not nome:
                        continue
                    nome_str = str(nome)
                    email_str = str(email or "")
                    if (
                        termo_norm in normalizar_para_busca(nome_str)
                        or normalizar_para_busca(nome_str) in termo_norm
                        or (email_str and termo_norm in normalizar_para_busca(email_str))
                    ):
                        return nome_str
        except Exception:
            pass
        return None

    async def criar_chamado(self, payload: MilvusCreateTicketPayload) -> Any:
        """Abre um novo chamado no Milvus (POST /chamado/criar) após confirmação do operador."""
        self._validar_rota("POST", "/chamado/criar")
        await self._aguardar_rate_limit()

        client = await self._obter_client()
        corpo = payload.to_clean_payload()

        resp = await client.post("/chamado/criar", json=corpo)
        resp.raise_for_status()
        return resp.json()

    async def buscar_id_interno_chamado(self, codigo_chamado: str | int) -> Optional[str | int]:
        """Consulta o chamado pelo código amigável para obter seu ID interno de 7/8 dígitos."""
        self._validar_rota("POST", "/chamado/listagem")
        await self._aguardar_rate_limit()

        client = await self._obter_client()
        resp = await client.post(
            "/chamado/listagem",
            params={"total_registros": 5, "pagina": 1},
            json={"filtro_body": {"codigo": str(codigo_chamado)}},
        )
        resp.raise_for_status()
        data = resp.json()
        items = data.get("lista", []) if isinstance(data, dict) else data
        if items and isinstance(items, list):
            for item in items:
                if isinstance(item, dict) and str(item.get("codigo")) == str(codigo_chamado):
                    return item.get("id")
            if isinstance(items[0], dict) and items[0].get("id"):
                return items[0].get("id")
        return None

    async def anexar_arquivo(
        self,
        chamado_id: str | int,
        caminho_arquivo: str | Path,
    ) -> Any:
        """Envia um anexo (print ou documento) para o chamado criado via multipart/form-data."""
        path_anexo = Path(caminho_arquivo).resolve()
        if not path_anexo.exists() or not path_anexo.is_file():
            raise FileNotFoundError(f"Arquivo anexo não encontrado: '{caminho_arquivo}'")

        endpoint = f"/chamado/anexo/criar/{chamado_id}"
        self._validar_rota("POST", endpoint)
        await self._aguardar_rate_limit()

        client = await self._obter_client()

        ext = path_anexo.suffix.lower()
        mime_type = "image/png" if ext == ".png" else "image/jpeg" if ext in [".jpg", ".jpeg"] else "application/octet-stream"

        with open(path_anexo, "rb") as f:
            conteudo = f.read()

        files = {"anexo": (path_anexo.name, conteudo, mime_type)}
        headers = {"Authorization": self.settings.milvus_api_token}
        resp = await client.post(endpoint, files=files, headers=headers)
        resp.raise_for_status()
        try:
            return resp.json()
        except Exception:
            return resp.text

    async def atribuir_operador(
        self,
        chamado_codigo_ou_id: str | int,
        email_tecnico: str,
    ) -> bool:
        """Atribui um operador/técnico a um chamado existente no Milvus via POST /chamado/atualizar.

        Args:
            chamado_codigo_ou_id: Código ou ID interno do chamado.
            email_tecnico: E-mail oficial do operador a ser atribuído.

        Returns:
            bool: True se a atribuição foi aceita com sucesso pela API do Milvus, False caso contrário.
        """
        self._validar_rota("POST", "/chamado/atualizar")
        await self._aguardar_rate_limit()

        client = await self._obter_client()
        corpo = {
            "chamado_ids": str(chamado_codigo_ou_id),
            "chamado_tecnico": str(email_tecnico).strip(),
        }

        try:
            resp = await client.post("/chamado/atualizar", json=corpo)
            resp.raise_for_status()
            data = resp.json()

            # Verificação de resposta lógica da API Milvus
            if isinstance(data, dict):
                if data.get("status") is False:
                    msg = data.get("mensagem") or data.get("retorno") or str(data)
                    console.print(
                        f"[bold red][Milvus Erro][/bold red] Falha ao atribuir chamado #{chamado_codigo_ou_id} "
                        f"ao operador {email_tecnico}: {msg}"
                    )
                    return False

            console.print(
                f"[bold green]✓ Operador {email_tecnico} atribuído com sucesso ao chamado #{chamado_codigo_ou_id} no Milvus.[/bold green]"
            )
            return True
        except httpx.HTTPStatusError as he:
            console.print(
                f"[bold red][Erro HTTP {he.response.status_code}][/bold red] ao atribuir chamado #{chamado_codigo_ou_id}: {he.response.text}"
            )
            return False
        except Exception as e:
            console.print(
                f"[bold red][Erro Inesperado][/bold red] ao atribuir chamado #{chamado_codigo_ou_id}: {e}"
            )
            return False

    async def atualizar_triagem_chamado(
        self,
        chamado_codigo_ou_id: str | int,
        prioridade: str | int,
        tipo_ticket: str,
        categoria_primaria: str,
        categoria_secundaria: Optional[str] = None,
        nome_operador: Optional[str] = None,
        email_tecnico: Optional[str] = None,
        contato_nome: Optional[str] = None,
        contato_email: Optional[str] = None,
        contato_telefone: Optional[str] = None,
    ) -> bool:
        """Atualiza a triagem completa e atribuição de um chamado no Milvus via POST /chamado/atualizar.

        Garante:
        1. chamado_prioridade recebe obrigatoriamente ID string numérico ('1', '2', '3' ou '4').
        2. Tríade de Atribuição no mesmo técnico com ordem estrita:
           - chamado_mesa: Nome do técnico
           - chamado_setor: Nome do técnico
           - chamado_tecnico: E-mail oficial do operador
        """
        self._validar_rota("POST", "/chamado/atualizar")
        await self._aguardar_rate_limit()

        # 1. Mapeamento Numérico Obrigatório da Prioridade ('1', '2', '3' ou '4')
        id_prioridade = resolver_prioridade_id(prioridade)

        # 2. Resolução do nome do operador caso venha apenas o e-mail
        nome_final = nome_operador
        if not nome_final and email_tecnico:
            nome_final = MAPA_EMAIL_PARA_NOME_OPERADOR.get(email_tecnico.strip().lower())

        # 3. Montagem do payload preservando a ordem das chaves na serialização JSON:
        # 1º chamado_ids
        # 2º chamado_mesa (Mesa de Trabalho)
        # 3º chamado_setor (Setor)
        # 4º chamado_tecnico (E-mail do operador)
        # 5º chamado_prioridade (ID '1', '2', '3' ou '4')
        # 6º chamado_tipo_chamado
        # 7º chamado_categoria_primaria
        # 8º chamado_categoria_secundaria
        corpo: dict[str, Any] = {
            "chamado_ids": str(chamado_codigo_ou_id),
        }

        if nome_final:
            corpo["chamado_mesa"] = str(nome_final).strip()
            corpo["chamado_setor"] = str(nome_final).strip()

        if email_tecnico:
            corpo["chamado_tecnico"] = str(email_tecnico).strip()

        corpo["chamado_prioridade"] = id_prioridade
        corpo["chamado_tipo_chamado"] = str(tipo_ticket).strip()
        corpo["chamado_categoria_primaria"] = str(categoria_primaria).strip()

        if categoria_secundaria:
            corpo["chamado_categoria_secundaria"] = str(categoria_secundaria).strip()

        if contato_nome:
            corpo["chamado_contato"] = str(contato_nome).strip()
        if contato_email:
            corpo["chamado_email"] = str(contato_email).strip()
        if contato_telefone:
            corpo["chamado_telefone"] = str(contato_telefone).strip()

        client = await self._obter_client()

        try:
            resp = await client.post("/chamado/atualizar", json=corpo)
            resp.raise_for_status()
            data = resp.json()

            if isinstance(data, dict):
                if data.get("status") is False:
                    msg = data.get("mensagem") or data.get("retorno") or str(data)
                    console.print(
                        f"[bold red][Milvus Erro][/bold red] Falha ao atualizar triagem do chamado #{chamado_codigo_ou_id}: {msg}"
                    )
                    return False

            console.print(
                f"[bold green]✓ Triagem do chamado #{chamado_codigo_ou_id} atualizada com sucesso no Milvus.[/bold green]"
            )
            return True
        except httpx.HTTPStatusError as he:
            console.print(
                f"[bold red][Erro HTTP {he.response.status_code}][/bold red] ao atualizar triagem do chamado #{chamado_codigo_ou_id}: {he.response.text}"
            )
            return False
        except Exception as e:
            console.print(
                f"[bold red][Erro Inesperado][/bold red] ao atualizar triagem do chamado #{chamado_codigo_ou_id}: {e}"
            )
            return False

    async def close(self) -> None:
        """Encerra a conexão HTTP."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    async def __aenter__(self) -> "MilvusTicketCreatorClient":
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()
