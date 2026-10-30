#!/usr/bin/env python3
"""Script de inicialização do Dashboard HUD Sci-Fi do A.N.A.K.I.N.

Executa o servidor web FastAPI/Uvicorn servindo a interface gráfica sci-fi e as rotas
de telemetria em tempo real com conexão somente leitura (read_only=True) ao DuckDB.

Uso:
    python run_hud.py
    python run_hud.py --port 8080
    python run_hud.py --port 7100 --host 0.0.0.0
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


def main() -> None:
    parser = argparse.ArgumentParser(description="A.N.A.K.I.N - Inicializador do HUD Sci-Fi")
    parser.add_argument("--host", default="127.0.0.1", help="Endereço de escuta (padrão: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=7100, help="Porta HTTP (padrão: 7100)")
    parser.add_argument("--reload", action="store_true", help="Ativa hot reload para desenvolvimento")
    args = parser.parse_args()

    print("=================================================================")
    print(" 🛸 A.N.A.K.I.N - SCI-FI HUD OPERATIONAL CONSOLE (v2.0)")
    print("=================================================================")
    print(f" [*] Modo: Somente Leitura (Read-Only) Ativo")
    print(f" [*] URL Local: http://{args.host}:{args.port}")
    print(f" [*] SSE Stream: http://{args.host}:{args.port}/api/stream")
    print(f" [*] DuckDB Cache: data/anakin_readonly.duckdb")
    print("=================================================================")
    print(" Pressione Ctrl+C para encerrar o servidor HUD.\n")

    from anakin.web.server import start_server
    start_server(host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()

