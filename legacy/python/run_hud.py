#!/usr/bin/env python3
"""Script de inicialização unificado do A.N.A.K.I.N (FastAPI + Worker de Despacho).

Executa em processo único nativo em Python:
1. Servidor Web FastAPI com Dashboard HUD Sci-Fi e streaming SSE (/api/stream).
2. Worker assíncrono em background para triagem autônoma via IA e despacho com rate limit.

Uso:
    python run_hud.py                         # Produção unificada completa
    python run_hud.py --simulacao             # Modo de simulação (sem alterar Milvus)
    python run_hud.py --simulacao --intervalo 30 # Ciclo de teste de 30 segundos
    python run_hud.py --sem-despachante       # Inicia somente o visualizador HUD sem despachar
"""

from pathlib import Path
import argparse
import sys

# Adiciona o diretório src ao sys.path
ROOT_DIR = Path(__file__).resolve().parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

# Configuração de encoding no Windows
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(
        description="A.N.A.K.I.N - Processo Único: HUD Sci-Fi Console & Despachante Automático com IA",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--host", default="127.0.0.1", help="Endereço de escuta (padrão: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=7100, help="Porta HTTP (padrão: 7100)")
    parser.add_argument("--reload", action="store_true", help="Ativa hot reload para desenvolvimento")
    parser.add_argument(
        "-s",
        "--simulacao",
        action="store_true",
        help="Executa em modo de simulação (avalia, tria e exibe sem persistir no Milvus).",
    )
    parser.add_argument(
        "--intervalo",
        type=int,
        default=62,
        help="Intervalo em segundos entre checagens na API do Milvus (padrão: 62s).",
    )
    parser.add_argument(
        "--sem-despachante",
        action="store_true",
        help="Inicia somente o visualizador HUD sem executar o despachante em background.",
    )

    args = parser.parse_args()

    from anakin.config.settings import get_settings
    settings = get_settings()

    modo_str = "SIMULAÇÃO (Sem mutações no Milvus)" if args.simulacao else "PRODUÇÃO AO VIVO (Alterações Ativas)"
    worker_str = "DESATIVADO (--sem-despachante)" if args.sem_despachante else f"ATIVO (Janela: {args.intervalo}s)"

    # Prevenção ativa de conflito: detecta se outra instância do A.N.A.K.I.N já está ativa
    import socket
    porta_ocupada = False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        if s.connect_ex((args.host, args.port)) == 0:
            porta_ocupada = True

    if porta_ocupada:
        print("=================================================================")
        print(f" [!] ALERTA: A porta {args.port} já está em uso por outro processo!")
        print("=================================================================")
        print(f" O A.N.A.K.I.N já está em execução e acessível em: http://{args.host}:{args.port}")
        print("\n Se deseja encerrar a instância anterior para reiniciar, execute no PowerShell:")
        print(f" Stop-Process -Id (Get-NetTCPConnection -LocalPort {args.port}).OwningProcess -Force\n")
        print("=================================================================")
        sys.exit(1)

    print("=================================================================")
    print(" 🛸 A.N.A.K.I.N - UNIFIED PROCESS (FASTAPI LIFESPAN + WORKER IA)")
    print("=================================================================")
    print(f" [*] Modo Operacional: {modo_str}")
    print(f" [*] Despachante IA:   {worker_str}")
    print(f" [*] URL Local HUD:    http://{args.host}:{args.port}")
    print(f" [*] Canal SSE Stream: http://{args.host}:{args.port}/api/stream")
    print(f" [*] Cache DuckDB:     {settings.duckdb_path}")
    print("=================================================================")
    print(" Pressione Ctrl+C para graceful shutdown seguro.\n")

    from anakin.web.server import start_server

    start_server(
        host=args.host,
        port=args.port,
        reload=args.reload,
        simulacao=args.simulacao,
        intervalo=args.intervalo,
        sem_despachante=args.sem_despachante,
    )


if __name__ == "__main__":
    main()
