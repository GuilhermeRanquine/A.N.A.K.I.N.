# 🛸 A.N.A.K.I.N
### **A**utomated **N**eural **A**ssignment, **K**nowledge **I**ntegration & **N**avigation
*Sistema Autônomo de Triagem Técnica por IA, Despacho Ponderado de Chamados e Telemetria em Tempo Real*

---

[![Python 3.11+](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.14-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg)](https://fastapi.tiangolo.com/)
[![DuckDB](https://img.shields.io/badge/DuckDB-1.1+-FFF000.svg)](https://duckdb.org/)
[![Google Gemini](https://img.shields.io/badge/Google%20Gemini-Flash--Lite-4285F4.svg)](https://ai.google.dev/)
[![Milvus Helpdesk API](https://img.shields.io/badge/Milvus-API%20Integrada-0052CC.svg)](https://milvus.com.br/)
[![License: Proprietary](https://img.shields.io/badge/License-Proprietary-red.svg)](#)

---

## 📋 Sumário Executivo

O **A.N.A.K.I.N** é uma plataforma corporativa desenvolvida para automatizar e otimizar a operação de suporte e Service Desk integrado ao **Milvus Helpdesk**. O sistema combina:

1. **Triagem Autônoma com IA Multimodal**: Leitura, interpretação técnica e classificação de criticidade de tickets utilizando **Google Gemini** (com fallback automático para **NVIDIA NIM / Minimax**).
2. **Algoritmo de Despacho Ponderado por Score de Carga**: Distribuição matemática equitativa de chamados entre os operadores elegíveis, eliminando sobrecargas e ociosidade.
3. **Roteamento em Camadas (Tiered Dispatching)**: Roteamento de incidentes graves (`Crítico` e `Alto`) para o pool Sênior / N2 e requisições/rotinas (`Médio` e `Baixo`) para o pool Pleno / N1, com balanceamento dinâmico.
4. **Camada de Persistência Analítica em DuckDB**: Banco analítico local de altíssima velocidade (`data/anakin_readonly.duckdb`), permitindo métricas e consultas com zero consumo desnecessário da cota da API.
5. **Dashboard Operacional Sci-Fi HUD (Web + Terminal Live)**: Interface futurista com telemetria via WebSockets/SSE e CLI dinâmico via Rich sem repetição de linhas.

---

## 🏛 Arquitetura do Sistema

```
                          ┌────────────────────────┐
                          │    Milvus Helpdesk     │
                          │   (API de Integração)  │
                          └───────────┬────────────┘
                                      │ (Rate Limit: 62s)
                                      ▼
                      ┌────────────────────────────────┐
                      │   MilvusTicketCreatorClient    │
                      │  - Resolução ID (chamado_ids)  │
                      │  - Tríade Mesa+Setor+Operador  │
                      │  - Retry de Prioridade Num/Txt │
                      └───────────────┬────────────────┘
                                      │
            ┌─────────────────────────┴─────────────────────────┐
            ▼                                                   ▼
┌─────────────────────────┐                           ┌────────────────────┐
│   TicketTriageAI        │                           │  AutoDispatcher    │
│  - Gemini / NVIDIA NIM  │                           │  - Fila Ponderada  │
│  - Prioridade Canônica  │                           │  - Pool Elegível   │
│  - Catálogo Homologado  │                           │  - Roteamento N1/N2│
└───────────┬─────────────┘                           └─────────┬──────────┘
            │                                                   │
            └─────────────────────────┬─────────────────────────┘
                                      ▼
                      ┌────────────────────────────────┐
                      │       LocalCache (DuckDB)      │
                      │   data/anakin_readonly.duckdb  │
                      │   - Operadores Oficiais        │
                      │   - Catálogo de Categorias     │
                      │   - Histórico e Status         │
                      └───────────────┬────────────────┘
                                      │
            ┌─────────────────────────┴─────────────────────────┐
            ▼                                                   ▼
┌─────────────────────────┐                           ┌────────────────────┐
│   CLI: dispatcher_aovivo│                           │ Sci-Fi Web HUD     │
│  - Terminal Rich Live   │                           │ - FastAPI + SSE/WS │
│  - Cronômetro Isolado   │                           │ - WebGL & Audio FX │
│  - Sem Pulo de Linhas   │                           │ - Cockpit Tático   │
└─────────────────────────┘                           └────────────────────┘
```

---

## ⚙️ Regras de Negócio e Algoritmos

### 1. Pool Elegível de Atendimento
A fila de distribuição automática é dividida em tiers técnicos balanceados:

| Perfil do Operador | Identificador Exemplo | Nível Técnico / Fila |
| :--- | :--- | :---: |
| **Operador Alpha** | `tecnico.senior1@empresa.com.br` | **Sênior / N2** |
| **Operador Bravo** | `tecnico.senior2@empresa.com.br` | **Sênior / N2** |
| **Operador Charlie** | `tecnico.senior3@empresa.com.br` | **Sênior / N2** |
| **Operador Delta** | `tecnico.pleno1@empresa.com.br` | **Pleno / N1** |
| **Operador Echo** | `tecnico.pleno2@empresa.com.br` | **Pleno / N1** |
| **Operador Foxtrot** | `tecnico.pleno3@empresa.com.br` | **Pleno / N1** |

> [!IMPORTANT]
> **Bloqueio de Gestão e Contas Administrativas**: Usuários com cargos de diretoria, coordenação e gerência são **estritamente bloqueados** da fila automática de recebimento de tickets por travas de segurança em código, preservando a governança operacional.

---

### 2. Algoritmo de Fila Ponderada por Carga (Score de Ocupação)
Cada operador tem seu Score calculado dinamicamente com base nos chamados ativos registrados no DuckDB:

$$\text{Score} = (\text{Qtd}_{\text{Atendendo}} \times 3) + (\text{Qtd}_{\text{A Fazer}} \times 2) + (\text{Qtd}_{\text{Pausado}} \times 1) + (\text{Qtd}_{\text{Outros}} \times 1)$$

* **Critério de Escolha**: O técnico do pool elegível com o **menor Score** recebe o chamado.
* **Regra de Desempate**: Em caso de empate no menor score, o sistema escolhe quem recebeu a última atribuição há mais tempo (Round-Robin temporal).

---

### 3. Roteamento por Criticidade e Nível (Tiered Dispatching)
A IA classifica a ocorrência em uma escala de quatro níveis:
* **Crítico / Alto**: Encaminhado prioritariamente para o **Pool Sênior / N2**.
* **Médio / Baixo**: Encaminhado prioritariamente para o **Pool Pleno / N1**.
* **Mecanismo de Alívio (Fallback Anti-Sobrecarga)**: Se o pool primário estiver sobrecarregado (diferença $\ge 10$ pontos em relação ao menor operador do outro pool), o chamado é redirecionado temporariamente para manter o SLA corporativo.

---

### 4. Tríade de Atribuição no Milvus
No endpoint `POST /api/chamado/atualizar`, o Milvus exige que a mesa e o setor acompanhem o operador. O A.N.A.K.I.N serializa estritamente:
```json
{
  "chamado_ids": "39236063",
  "chamado_mesa": "Operador Delta",
  "chamado_setor": "Operador Delta",
  "chamado_tecnico": "tecnico.pleno1@empresa.com.br",
  "chamado_prioridade": "Médio",
  "chamado_categoria_primaria": "Computadores",
  "chamado_categoria_secundaria": "Lentidão",
  "chamado_tipo_chamado": "Incidente"
}
```

* **Retry Resiliente**: Caso o Milvus rejeite a prioridade em texto (`"Médio"`), o cliente detecta o erro e executa um retry instantâneo com o identificador numérico correspondente (`"2"`).

---

## 🚀 Instalação e Configuração

### 1. Clonar o Repositório
```bash
git clone https://github.com/GuilhermeRanquine/A.N.A.K.I.N..git
cd A.N.A.K.I.N
```

### 2. Configurar o Ambiente Virtual
```bash
python -m venv .venv
# No Windows PowerShell:
.\.venv\Scripts\Activate.ps1
# No Linux/macOS:
source .venv/bin/activate
```

### 3. Instalar Dependências
```bash
pip install -r requirements.txt
```

### 4. Configurar Variáveis de Ambiente (`.env`)
Copie o arquivo de exemplo ou crie seu `.env` na raiz:
```bash
cp .env.example .env
```

Edite o arquivo `.env` com suas credenciais:
```ini
# Integração Milvus Helpdesk
MILVUS_BASE_URL=https://apiintegracao.milvus.com.br/api
MILVUS_API_TOKEN=seu_token_api_milvus
MILVUS_RATE_LIMIT_SECONDS=62

# Inteligência Artificial
GEMINI_API_KEY=sua_chave_gemini_google
GEMINI_MODEL=gemini-2.5-flash-lite
NVIDIA_API_KEY=sua_chave_nvidia_nim_fallback

# Pesos do Algoritmo de Carga
PESO_STATUS_ATENDENDO=3
PESO_STATUS_A_FAZER=2
PESO_STATUS_PAUSADO=1
PESO_STATUS_OUTROS=1

# Configurações do Servidor HUD
HUD_HOST=127.0.0.1
HUD_PORT=7100
```

---

## 🖥 Como Utilizar

### Modo 1: Despachante Automático ao Vivo (Terminal CLI)
Executa o loop contínuo de monitoramento, triagem e despacho com interface Rich dinâmica e cronômetro isolado:

```bash
# Execução em Produção (alterações reais no Milvus):
python dispatcher_aovivo.py

# Execução em Modo de Simulação (avalia e simula sem modificar o Milvus):
python dispatcher_aovivo.py --simulacao

# Definindo intervalo customizado (ex: 30 segundos em laboratório):
python dispatcher_aovivo.py --simulacao --intervalo 30
```

---

### Modo 2: Console Tático Web (Sci-Fi HUD)
Inicia o dashboard gráfico web com telemetria em tempo real, painel de operadores, métricas de SLA e áudio sintetizado:

```bash
python run_hud.py
```
Acesse no seu navegador: **`http://127.0.0.1:7100`**

---

### Modo 3: Atendente Inteligente via Terminal
Permite abrir chamados guiados por IA através de screenshots ou texto com extração de dados do solicitante:

```bash
python anakin_atendente.py
```

---

## 🧪 Testes Unitários

O projeto conta com uma suíte abrangente de testes automatizados:

```bash
# Executar todos os testes:
python -m unittest discover -s tests -p "test_*.py"

# Executar testes específicos do despachante:
python -m unittest tests/test_auto_dispatcher.py

# Executar testes do servidor HUD:
python -m unittest tests/test_hud_server.py
```

---

## 🛡️ Segurança e Governança

* **Proteção contra Sobrecarga**: O `AsyncLimiter(1, 62)` garante que a API do Milvus nunca sofra bloqueio por rate limit.
* **Integridade de Clientes**: O cliente `MilvusTicketCreatorClient` bloqueia preventivamente qualquer rota de modificação cadastral (`PUT`, `DELETE` ou `/cliente/editar`).
* **Conexão DuckDB Isolada**: As rotas de leitura do HUD operam com `read_only=True`, impedindo locks no banco de dados operacional.
* **Sanitização de Logs**: Respostas de erro da API são sanitizadas via `rich.markup.escape()` para evitar injeções ou corrupção do terminal.

---

## 👥 Governança do Projeto

Projeto arquitetado e mantido sob padrões estritos de automação autônoma, segurança operacional e integridade de dados.
