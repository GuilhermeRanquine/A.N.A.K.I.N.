/**
 * A.N.A.K.I.N Sci-Fi HUD Application (hud-app.js)
 * Conexão com a API REST, streaming SSE e renderização reativa dos componentes HUD.
 */

import { refreshPalette, pal } from "./core/palette.js";
import { startSparks } from "./core/sparks.js";

// Estado global da aplicação
const state = {
  theme: "ember",
  scheme: "dark",
  sparksInstance: null,
  currentPage: 1,
  pageSize: 15,
  totalPages: 1,
  statusFilter: "",
  searchQuery: "",
  sseSource: null,
  terminalPaused: false,
  ticketsCache: new Map(),
};

// =====================================================================
// 1. INICIALIZAÇÃO DE PALETAS, SPARKS E TEMAS
// =====================================================================
function initThemeAndPalette() {
  const sparksCanvas = document.getElementById("sparks");
  if (sparksCanvas) {
    try {
      state.sparksInstance = startSparks(sparksCanvas, {
        density: 120,
        ratio: 0.18,
        palette: ["255,154,60"],
        speed: 1.1,
        cometEvery: 2.2,
        opacity: 0.55,
      });
    } catch (e) {
      console.warn("Sparks canvas notice:", e);
    }
  }

  // Seletor de Tema
  const themeButtons = document.querySelectorAll("#theme-selector button");
  themeButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      const theme = btn.dataset.theme;
      setTheme(theme);
    });
  });

  // Seletor de Scheme (Dark / Light)
  const schemeButtons = document.querySelectorAll("#scheme-selector button");
  schemeButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      const scheme = btn.dataset.scheme;
      setScheme(scheme);
    });
  });

  // Carrega preferências salvas
  try {
    const savedTheme = localStorage.getItem("anakin-hud-theme") || "ember";
    const savedScheme = localStorage.getItem("anakin-hud-scheme") || "dark";
    setTheme(savedTheme, false);
    setScheme(savedScheme, false);
  } catch {
    setTheme("ember", false);
    setScheme("dark", false);
  }
}

function setTheme(theme, save = true) {
  state.theme = theme;
  document.documentElement.dataset.theme = theme;

  const root = document.documentElement;
  if (theme === "ember") {
    root.style.setProperty("--accent", "#ff9a3c");
    root.style.setProperty("--accent-rgb", "255, 154, 60");
    if (state.sparksInstance) {
      state.sparksInstance.setConfig({ palette: ["255,154,60"] });
    }
  } else if (theme === "cinder") {
    root.style.setProperty("--accent", "#ff4d3d");
    root.style.setProperty("--accent-rgb", "255, 77, 61");
    if (state.sparksInstance) {
      state.sparksInstance.setConfig({ palette: ["255,77,61", "255,154,60"] });
    }
  } else if (theme === "orbital") {
    root.style.setProperty("--accent", "#38bdf8");
    root.style.setProperty("--accent-rgb", "56, 189, 248");
    if (state.sparksInstance) {
      state.sparksInstance.setConfig({ palette: ["56,189,248", "125,211,252"] });
    }
  }

  document.querySelectorAll("#theme-selector button").forEach((b) => {
    b.classList.toggle("on", b.dataset.theme === theme);
  });

  if (save) {
    try { localStorage.setItem("anakin-hud-theme", theme); } catch {}
  }
  refreshPalette();
}

function setScheme(scheme, save = true) {
  state.scheme = scheme;
  document.documentElement.dataset.scheme = scheme;

  document.querySelectorAll("#scheme-selector button").forEach((b) => {
    b.classList.toggle("on", b.dataset.scheme === scheme);
  });

  if (save) {
    try { localStorage.setItem("anakin-hud-scheme", scheme); } catch {}
  }
  refreshPalette();
}

// =====================================================================
// 2. RELÓGIO HUD SINCRONIZADO
// =====================================================================
function startClock() {
  const timeEl = document.getElementById("clock-time");
  const dateEl = document.getElementById("clock-date");

  function update() {
    const now = new Date();
    if (timeEl) {
      const h = String(now.getHours()).padStart(2, "0");
      const m = String(now.getMinutes()).padStart(2, "0");
      const s = String(now.getSeconds()).padStart(2, "0");
      timeEl.textContent = `${h}:${m}:${s}`;
    }
    if (dateEl) {
      const d = String(now.getDate()).padStart(2, "0");
      const mo = String(now.getMonth() + 1).padStart(2, "0");
      const y = now.getFullYear();
      dateEl.textContent = `${d}/${mo}/${y}`;
    }
  }
  update();
  setInterval(update, 1000);
}

// =====================================================================
// 3. CONSULTAS À API REST & ATUALIZAÇÃO DO DOM
// =====================================================================
async function carregarStatus() {
  try {
    const res = await fetch("/api/status");
    if (!res.ok) return;
    const data = await res.json();
    renderStatus(data);
  } catch (err) {
    console.warn("Aviso ao carregar status:", err);
  }
}

function renderStatus(data) {
  if (!data || !data.kpis) return;
  const k = data.kpis;

  const setVal = (id, val) => {
    const el = document.getElementById(id);
    if (el) el.textContent = val;
  };

  setVal("kpi-total", k.total_tickets || 0);
  setVal("kpi-abertos", k.total_abertos || 0);
  setVal("kpi-atendendo", k.atendendo || 0);
  setVal("kpi-afazer", k.a_fazer || 0);
  setVal("kpi-pausado", k.pausado || 0);
  setVal("kpi-finalizados", k.finalizados_hoje || 0);
  setVal("kpi-sla", `${k.taxa_conformidade_pct || 0}%`);

  const slaTrend = document.getElementById("kpi-sla-trend");
  if (slaTrend) {
    if (k.taxa_conformidade_pct >= 85) {
      slaTrend.textContent = "NOMINAL (EXCELENTE)";
      slaTrend.className = "trend up";
    } else if (k.taxa_conformidade_pct >= 60) {
      slaTrend.textContent = "ATENÇÃO (MODERADO)";
      slaTrend.className = "trend";
    } else {
      slaTrend.textContent = "CRÍTICO (ESTOURADO)";
      slaTrend.className = "trend down";
    }
  }
}

async function carregarPool() {
  try {
    const res = await fetch("/api/pool");
    if (!res.ok) return;
    const data = await res.json();
    renderPool(data);
  } catch (err) {
    console.warn("Aviso ao carregar pool:", err);
  }
}

function renderPool(data) {
  if (!data || !data.operadores) return;

  const poolSummary = document.getElementById("pool-next-summary");
  if (poolSummary) {
    poolSummary.textContent = `${data.operadores.length} OPERADORES ELEGÍVEIS`;
  }

  const nextN1El = document.getElementById("next-n1-name");
  if (nextN1El) nextN1El.textContent = data.proximo_n1 || "NENHUM";

  const nextN2El = document.getElementById("next-n2-name");
  if (nextN2El) nextN2El.textContent = data.proximo_n2 || "NENHUM";

  const container = document.getElementById("pool-list");
  if (!container) return;

  container.innerHTML = "";

  // Encontra o score máximo para a barra de proporção
  const maxScore = Math.max(...data.operadores.map((o) => o.score_total), 1);

  data.operadores.forEach((op) => {
    const isNext = op.eh_proximo_n1 || op.eh_proximo_n2;
    const pct = Math.min(100, Math.round((op.score_total / (maxScore * 1.2)) * 100));

    const card = document.createElement("div");
    card.className = `pool-card ${isNext ? "proximo" : ""}`;

    const tagBadge = isNext
      ? `<span class="hud-badge solid" style="background:var(--ac);color:var(--bg)">PRÓXIMO</span>`
      : `<span class="hud-badge">${op.nivel}</span>`;

    card.innerHTML = `
      <div class="pool-card-header">
        <b class="pool-card-name">${op.nome}</b>
        <div style="display:flex;align-items:center;gap:6px;">
          ${tagBadge}
          <span class="pool-card-score">SCORE: ${op.score_total}</span>
        </div>
      </div>
      <div class="hud-progress thin" style="margin:2px 0;">
        <i style="width:${pct}%;background:${isNext ? "var(--ac)" : "var(--fg)"}"></i>
      </div>
      <div class="pool-card-sub">
        <span>${op.detalhe}</span>
        <span>${op.total_abertos} abertos</span>
      </div>
    `;
    container.appendChild(card);
  });
}

async function carregarTickets() {
  const tbody = document.getElementById("tickets-tbody");
  if (tbody && state.currentPage === 1 && !tbody.hasChildNodes()) {
    tbody.innerHTML = `<tr><td colspan="8" style="text-align:center;color:var(--dim);padding:24px;">Consultando chamados do DuckDB...</td></tr>`;
  }

  try {
    const params = new URLSearchParams({
      page: String(state.currentPage),
      page_size: String(state.pageSize),
      apenas_abertos: "true",
    });

    if (state.statusFilter) params.append("status", state.statusFilter);
    if (state.searchQuery) params.append("busca", state.searchQuery);

    const res = await fetch(`/api/tickets?${params.toString()}`);
    if (!res.ok) return;

    const data = await res.json();
    renderTickets(data);
  } catch (err) {
    console.warn("Aviso ao carregar chamados:", err);
  }
}

function renderTickets(data) {
  const tbody = document.getElementById("tickets-tbody");
  if (!tbody) return;

  state.totalPages = data.total_paginas || 1;
  state.currentPage = data.page || 1;

  const countBadge = document.getElementById("tickets-count-badge");
  if (countBadge) {
    countBadge.textContent = `${data.total_registros || 0} REGISTROS`;
  }

  const pageInfo = document.getElementById("page-info");
  if (pageInfo) {
    pageInfo.textContent = `Página ${state.currentPage} de ${state.totalPages} (${data.total_registros || 0} chamados)`;
  }

  updatePagerControls();

  tbody.innerHTML = "";

  if (!data.itens || data.itens.length === 0) {
    tbody.innerHTML = `<tr><td colspan="8" style="text-align:center;color:var(--dim);padding:30px;">Nenhum chamado encontrado para o filtro atual.</td></tr>`;
    return;
  }

  // Armazena no cache local para abertura rápida no modal
  state.ticketsCache.clear();
  data.itens.forEach((t) => {
    state.ticketsCache.set(String(t.codigo), t);

    const tr = document.createElement("tr");

    // Classe de prioridade
    const pNorm = (t.prioridade_norm || "").toLowerCase();
    let pClass = "baixo";
    if (pNorm.includes("crit")) pClass = "critico";
    else if (pNorm.includes("alt")) pClass = "alto";
    else if (pNorm.includes("med")) pClass = "medio";

    // Classe de SLA
    const slaNorm = (t.sla_solucao_norm || "").toLowerCase();
    let slaClass = "conformidade";
    if (slaNorm.includes("estour") || slaNorm.includes("venc")) slaClass = "estourado";
    else if (slaNorm.includes("prest") || slaNorm.includes("aten")) slaClass = "prestes";

    // Assunto encurtado
    const assuntoTxt = t.assunto || "Sem assunto informado";
    const assuntoCurto = assuntoTxt.length > 42 ? assuntoTxt.slice(0, 39) + "..." : assuntoTxt;

    tr.innerHTML = `
      <td style="color:var(--ac);font-weight:300;">#${t.codigo}</td>
      <td><span class="badge-prio ${pClass}">${t.prioridade_norm || "-"}</span></td>
      <td style="color:var(--fg);max-width:140px;overflow:hidden;text-overflow:ellipsis;">${t.cliente || "-"}</td>
      <td title="${escapeHtml(assuntoTxt)}">${escapeHtml(assuntoCurto)}</td>
      <td style="color:var(--dim);">${t.tecnico || "Sem técnico"}</td>
      <td><span class="hud-badge">${t.status || "-"}</span></td>
      <td><span class="badge-sla ${slaClass}">${t.sla_solucao_norm || "-"}</span></td>
      <td class="num" style="color:var(--dim);">${t.tempo_aberto || "-"}</td>
    `;

    tr.addEventListener("click", () => {
      abrirModalChamado(t);
    });

    tbody.appendChild(tr);
  });
}

function updatePagerControls() {
  const prevBtn = document.getElementById("btn-prev-page");
  const nextBtn = document.getElementById("btn-next-page");
  const numbersSpan = document.getElementById("pager-numbers");

  if (prevBtn) prevBtn.disabled = state.currentPage <= 1;
  if (nextBtn) nextBtn.disabled = state.currentPage >= state.totalPages;

  if (numbersSpan) {
    numbersSpan.innerHTML = "";
    const start = Math.max(1, state.currentPage - 2);
    const end = Math.min(state.totalPages, start + 4);

    for (let p = start; p <= end; p++) {
      const btn = document.createElement("button");
      btn.textContent = p;
      btn.className = p === state.currentPage ? "on" : "";
      btn.addEventListener("click", () => {
        state.currentPage = p;
        carregarTickets();
      });
      numbersSpan.appendChild(btn);
    }
  }
}

async function carregarMetricas() {
  try {
    const res = await fetch("/api/metrics");
    if (!res.ok) return;
    const data = await res.json();
    renderMetricas(data);
  } catch (err) {
    console.warn("Aviso ao carregar métricas:", err);
  }
}

function renderMetricas(data) {
  // 1. Barras de SLA
  const slaContainer = document.getElementById("sla-bars");
  if (slaContainer && data.sla) {
    slaContainer.innerHTML = "";
    data.sla.forEach((item) => {
      const row = document.createElement("div");
      row.className = "hud-bar-row";
      const pct = item.percentual || 0;
      let corBarra = "var(--fg)";
      if (item.status_sla === "Em conformidade") corBarra = "#4ade80";
      else if (item.status_sla === "Prestes a estourar") corBarra = "#fbbf24";
      else if (item.status_sla === "Estourado") corBarra = "var(--danger)";

      row.innerHTML = `
        <span class="lbl" title="${item.status_sla}">${item.status_sla}</span>
        <div class="hud-progress"><i style="width:${pct}%;background:${corBarra}"></i></div>
        <span class="val">${item.quantidade} (${pct}%)</span>
      `;
      slaContainer.appendChild(row);
    });
  }

  // 2. Barras de Prioridades
  const prioContainer = document.getElementById("prio-bars");
  if (prioContainer && data.prioridades) {
    prioContainer.innerHTML = "";
    data.prioridades.forEach((item) => {
      const row = document.createElement("div");
      row.className = "hud-bar-row";
      const pct = item.percentual || 0;
      row.innerHTML = `
        <span class="lbl">${item.prioridade}</span>
        <div class="hud-progress thin"><i style="width:${pct}%;background:var(--ac)"></i></div>
        <span class="val">${item.quantidade}</span>
      `;
      prioContainer.appendChild(row);
    });
  }

  // 3. Top Categorias
  const catContainer = document.getElementById("category-bars");
  if (catContainer && data.categorias) {
    catContainer.innerHTML = "";
    const maxQtd = Math.max(...data.categorias.map((c) => c.quantidade), 1);
    data.categorias.forEach((cat) => {
      const row = document.createElement("div");
      row.className = "hud-bar-row";
      const pct = Math.round((cat.quantidade / maxQtd) * 100);
      row.innerHTML = `
        <span class="lbl" title="${cat.categoria}">${cat.categoria}</span>
        <div class="hud-progress thin"><i style="width:${pct}%;background:var(--fg)"></i></div>
        <span class="val">${cat.quantidade}</span>
      `;
      catContainer.appendChild(row);
    });
  }

  // 4. Outliers de Clientes
  const outliersContainer = document.getElementById("outliers-list");
  if (outliersContainer && data.outliers_clientes) {
    outliersContainer.innerHTML = "";
    data.outliers_clientes.forEach((cli) => {
      const row = document.createElement("div");
      row.style.display = "flex";
      row.style.justifyContent = "space-between";
      row.style.alignItems = "center";
      row.style.padding = "3px 0";
      row.style.fontSize = "var(--fs-xs)";

      const badgeOutlier = cli.is_outlier
        ? `<span class="hud-badge danger" style="padding:1px 4px;">ANOMALIA (Z=${cli.z_score})</span>`
        : `<span class="hud-badge" style="padding:1px 4px;">Z=${cli.z_score}</span>`;

      row.innerHTML = `
        <span style="color:var(--dim);max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${cli.cliente}">
          ${cli.cliente}
        </span>
        <div style="display:flex;align-items:center;gap:6px;">
          ${badgeOutlier}
          <b style="color:var(--fg);font-variant-numeric:tabular-nums;">${cli.total_chamados}</b>
        </div>
      `;
      outliersContainer.appendChild(row);
    });
  }
}

// =====================================================================
// 4. STREAMING EM TEMPO REAL VIA SSE
// =====================================================================
function initSSE() {
  if (state.sseSource) {
    state.sseSource.close();
  }

  try {
    state.sseSource = new EventSource("/api/stream");

    state.sseSource.addEventListener("status", (event) => {
      try {
        const data = JSON.parse(event.data);
        renderStatus(data);
      } catch (e) {
        console.error("Erro ao parsear status SSE:", e);
      }
    });

    state.sseSource.addEventListener("pool", (event) => {
      try {
        const data = JSON.parse(event.data);
        renderPool(data);
      } catch (e) {
        console.error("Erro ao parsear pool SSE:", e);
      }
    });

    state.sseSource.addEventListener("logs_batch", (event) => {
      try {
        const logs = JSON.parse(event.data);
        const termBody = document.getElementById("terminal-body");
        if (termBody && Array.isArray(logs)) {
          termBody.innerHTML = "";
          logs.forEach((l) => appendTerminalLine(l, false));
          termBody.scrollTop = termBody.scrollHeight;
        }
      } catch (e) {
        console.error("Erro ao parsear logs_batch SSE:", e);
      }
    });

    state.sseSource.addEventListener("log", (event) => {
      try {
        const log = JSON.parse(event.data);
        appendTerminalLine(log, true);
      } catch (e) {
        console.error("Erro ao parsear log SSE:", e);
      }
    });

    state.sseSource.onerror = () => {
      console.warn("Conexão SSE oscilou. Reconectando em 5 segundos...");
    };
  } catch (err) {
    console.error("Falha ao inicializar EventSource SSE:", err);
  }
}

function appendTerminalLine(log, autoScroll = true) {
  if (state.terminalPaused) return;

  const termBody = document.getElementById("terminal-body");
  if (!termBody) return;

  const div = document.createElement("div");
  div.className = "term-line";

  const hora = log.horario || "--:--:--";
  const origem = (log.origem || "SYSTEM").toLowerCase();
  const nivel = (log.nivel || "INFO").toUpperCase();

  let srcClass = "kernel";
  if (origem.includes("dispatch")) srcClass = "";
  else if (origem.includes("atendente")) srcClass = "atendente";
  else if (origem.includes("milvus")) srcClass = "milvus";

  let nivelColor = "var(--dim)";
  if (nivel === "SUCCESS") nivelColor = "#4ade80";
  else if (nivel === "WARN") nivelColor = "#fbbf24";
  else if (nivel === "ERROR") nivelColor = "var(--danger)";

  div.innerHTML = `
    <span class="term-time">[${hora}]</span>
    <span class="term-src ${srcClass}">[${log.origem || "SYS"}]</span>
    <span style="color:${nivelColor}">•</span>
    <span class="term-msg">${escapeHtml(log.mensagem || "")}</span>
  `;

  termBody.appendChild(div);

  // Mantém no máximo 250 linhas para evitar sobrecarga no DOM
  while (termBody.children.length > 250) {
    termBody.removeChild(termBody.firstChild);
  }

  if (autoScroll) {
    termBody.scrollTop = termBody.scrollHeight;
  }
}

// =====================================================================
// 5. MODAL DE DETALHES DO CHAMADO
// =====================================================================
function abrirModalChamado(ticket) {
  const modal = document.getElementById("ticket-modal");
  const modalCod = document.getElementById("modal-codigo");
  const modalCli = document.getElementById("modal-cliente");
  const modalBody = document.getElementById("modal-details-body");

  if (!modal || !modalBody) return;

  modalCod.textContent = `CHAMADO #${ticket.codigo}`;
  modalCli.textContent = ticket.cliente || "Cliente não informado";

  modalBody.innerHTML = `
    <div class="modal-detail-row"><span class="lbl">Assunto:</span><span class="val" style="color:var(--ac);font-weight:300;">${escapeHtml(ticket.assunto || "-")}</span></div>
    <div class="modal-detail-row"><span class="lbl">Status:</span><span class="val"><span class="hud-badge solid">${ticket.status || "-"}</span></span></div>
    <div class="modal-detail-row"><span class="lbl">Prioridade:</span><span class="val"><span class="badge-prio">${ticket.prioridade_norm || "-"}</span></span></div>
    <div class="modal-detail-row"><span class="lbl">Técnico:</span><span class="val">${ticket.tecnico || "Sem técnico"} (${ticket.email_tecnico || "sem e-mail"})</span></div>
    <div class="modal-detail-row"><span class="lbl">Mesa / Setor:</span><span class="val">${ticket.mesa_trabalho || "-"}</span></div>
    <div class="modal-detail-row"><span class="lbl">Categoria:</span><span class="val">${ticket.categoria_primaria || "-"} &gt; ${ticket.categoria_secundaria || "-"}</span></div>
    <div class="modal-detail-row"><span class="lbl">SLA Solução:</span><span class="val">${ticket.sla_solucao_norm || "-"}</span></div>
    <div class="modal-detail-row"><span class="lbl">Data Abertura:</span><span class="val">${ticket.data_criacao || "-"} (aberto há ${ticket.tempo_aberto || "-"})</span></div>
    <div class="modal-detail-row"><span class="lbl">ID Interno:</span><span class="val" style="font-family:monospace;color:var(--dim);">${ticket.id || "-"}</span></div>
  `;

  modal.style.display = "grid";
}

function fecharModal() {
  const modal = document.getElementById("ticket-modal");
  if (modal) modal.style.display = "none";
}

// =====================================================================
// 6. EVENT LISTENERS E UTILITÁRIOS
// =====================================================================
function setupEventListeners() {
  // Busca
  const searchInput = document.getElementById("search-input");
  const btnSearch = document.getElementById("btn-search");
  if (btnSearch && searchInput) {
    btnSearch.addEventListener("click", () => {
      state.searchQuery = searchInput.value.trim();
      state.currentPage = 1;
      carregarTickets();
    });
    searchInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        state.searchQuery = searchInput.value.trim();
        state.currentPage = 1;
        carregarTickets();
      }
    });
  }

  // Segmented Status Filter
  const statusButtons = document.querySelectorAll("#status-filter button");
  statusButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      statusButtons.forEach((b) => b.classList.remove("on"));
      btn.classList.add("on");
      state.statusFilter = btn.dataset.status || "";
      state.currentPage = 1;
      carregarTickets();
    });
  });

  // Botão Refresh
  const btnRefresh = document.getElementById("btn-refresh");
  if (btnRefresh) {
    btnRefresh.addEventListener("click", () => {
      carregarStatus();
      carregarPool();
      carregarTickets();
      carregarMetricas();
    });
  }

  // Paginação
  const prevBtn = document.getElementById("btn-prev-page");
  if (prevBtn) {
    prevBtn.addEventListener("click", () => {
      if (state.currentPage > 1) {
        state.currentPage--;
        carregarTickets();
      }
    });
  }

  const nextBtn = document.getElementById("btn-next-page");
  if (nextBtn) {
    nextBtn.addEventListener("click", () => {
      if (state.currentPage < state.totalPages) {
        state.currentPage++;
        carregarTickets();
      }
    });
  }

  // Terminal Controls
  const btnPauseTerm = document.getElementById("btn-pause-term");
  if (btnPauseTerm) {
    btnPauseTerm.addEventListener("click", () => {
      state.terminalPaused = !state.terminalPaused;
      btnPauseTerm.textContent = state.terminalPaused ? "RETOMAR" : "PAUSAR";
      btnPauseTerm.classList.toggle("accent", state.terminalPaused);
    });
  }

  const btnClearTerm = document.getElementById("btn-clear-term");
  if (btnClearTerm) {
    btnClearTerm.addEventListener("click", () => {
      const termBody = document.getElementById("terminal-body");
      if (termBody) termBody.innerHTML = "";
    });
  }

  // Modal Fechar
  const btnCloseModal = document.getElementById("btn-close-modal");
  if (btnCloseModal) {
    btnCloseModal.addEventListener("click", fecharModal);
  }

  const modalBack = document.getElementById("ticket-modal");
  if (modalBack) {
    modalBack.addEventListener("click", (e) => {
      if (e.target === modalBack) fecharModal();
    });
  }

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") fecharModal();
  });
}

function escapeHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

// =====================================================================
// 7. BOOTSTRAP DA APLICAÇÃO
// =====================================================================
document.addEventListener("DOMContentLoaded", () => {
  initThemeAndPalette();
  startClock();
  setupEventListeners();

  // Carga inicial
  carregarStatus();
  carregarPool();
  carregarTickets();
  carregarMetricas();

  // Inicia streaming contínuo
  initSSE();

  // Polling de fallback leve a cada 15 segundos para os chamados da tabela
  setInterval(() => {
    carregarTickets();
    carregarMetricas();
  }, 15000);
});

