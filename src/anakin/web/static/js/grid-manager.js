/**
 * A.N.A.K.I.N Grid Customization & Drag-and-Drop Manager (grid-manager.js)
 * Permite movimentar janelas, personalizar visualizações, minimizar e salvar layouts.
 */

const STORAGE_KEY = "anakin_hud_grid_layout_v3";

export class HudGridManager {
  constructor() {
    this.grid = document.querySelector(".dashboard-grid");
    this.columns = document.querySelectorAll(".grid-column");
    this.draggedPanel = null;
    this.config = this.loadConfig();

    this.initPanelControls();
    this.initDragAndDrop();
    this.initCustomizerDropdown();
    this.applyConfig();
  }

  loadConfig() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      return raw ? JSON.parse(raw) : {};
    } catch {
      return {};
    }
  }

  saveConfig() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(this.config));
    } catch (e) {
      console.warn("Aviso ao salvar layout do grid:", e);
    }
  }

  initPanelControls() {
    const panels = document.querySelectorAll(".hud-panel");
    panels.forEach((panel) => {
      const header = panel.querySelector("header");
      if (!header) return;

      const widgetId = panel.dataset.widgetId || panel.id || "widget-" + Math.random().toString(36).substring(7);
      panel.dataset.widgetId = widgetId;

      // 1. Alça de Arraste (Grip)
      if (!header.querySelector(".hud-drag-handle")) {
        const grip = document.createElement("span");
        grip.className = "hud-drag-handle";
        grip.innerHTML = "⠿ ARRASTAR";
        grip.title = "Clique e arraste esta janela para qualquer coluna do grid";
        header.insertBefore(grip, header.firstChild);
      }

      // 2. Ações de Janela (Navegação Rápida entre Colunas + Minimizar + Fechar)
      if (!header.querySelector(".hud-window-actions")) {
        const actions = document.createElement("div");
        actions.className = "hud-window-actions";
        actions.innerHTML = `
          <button class="win-btn win-move-left" title="Mover para coluna da esquerda">◀</button>
          <button class="win-btn win-move-right" title="Mover para coluna da direita">▶</button>
          <button class="win-btn win-min" title="Minimizar / Expandir corpo">_</button>
          <button class="win-btn win-close" title="Ocultar do grid">✕</button>
        `;
        header.appendChild(actions);

        // Mover para coluna da esquerda
        actions.querySelector(".win-move-left").addEventListener("click", (e) => {
          e.preventDefault();
          e.stopPropagation();
          this.movePanelColumn(panel, -1);
        });

        // Mover para coluna da direita
        actions.querySelector(".win-move-right").addEventListener("click", (e) => {
          e.preventDefault();
          e.stopPropagation();
          this.movePanelColumn(panel, 1);
        });

        // Minimizar
        actions.querySelector(".win-min").addEventListener("click", (e) => {
          e.preventDefault();
          e.stopPropagation();
          panel.classList.toggle("minimized");
          this.config[widgetId] = this.config[widgetId] || {};
          this.config[widgetId].minimized = panel.classList.contains("minimized");
          this.saveConfig();
        });

        // Ocultar
        actions.querySelector(".win-close").addEventListener("click", (e) => {
          e.preventDefault();
          e.stopPropagation();
          panel.style.display = "none";
          this.config[widgetId] = this.config[widgetId] || {};
          this.config[widgetId].hidden = true;
          this.saveConfig();
          this.syncCustomizerUI();
        });
      }

      // Evita conflito entre clique nos botões e o dragstart do header
      header.querySelectorAll("button, input, a").forEach((btn) => {
        btn.addEventListener("mousedown", () => header.setAttribute("draggable", "false"));
        btn.addEventListener("mouseup", () => header.setAttribute("draggable", "true"));
        btn.addEventListener("mouseleave", () => header.setAttribute("draggable", "true"));
      });

      // Ativa arrasto nativo no header
      header.setAttribute("draggable", "true");
    });
  }

  movePanelColumn(panel, direction) {
    const colList = Array.from(document.querySelectorAll(".grid-column"));
    const currentCol = panel.closest(".grid-column");
    if (!currentCol) return;

    const currentIndex = colList.indexOf(currentCol);
    const targetIndex = currentIndex + direction;

    if (targetIndex >= 0 && targetIndex < colList.length) {
      const targetCol = colList[targetIndex];
      targetCol.insertBefore(panel, targetCol.firstChild);
      this.recordOrder();
    }
  }

  initDragAndDrop() {
    const panels = document.querySelectorAll(".hud-panel");
    const columns = document.querySelectorAll(".grid-column");

    panels.forEach((panel) => {
      const header = panel.querySelector("header");
      if (!header) return;

      header.addEventListener("dragstart", (e) => {
        if (e.target.closest("button") || e.target.closest("input") || e.target.closest(".holo-mode-btn")) {
          e.preventDefault();
          return;
        }
        this.draggedPanel = panel;
        panel.classList.add("dragging");
        e.dataTransfer.effectAllowed = "move";
        e.dataTransfer.setData("text/plain", panel.dataset.widgetId);
      });

      header.addEventListener("dragend", () => {
        if (this.draggedPanel) {
          this.draggedPanel.classList.remove("dragging");
          this.draggedPanel = null;
        }
        document.querySelectorAll(".hud-panel").forEach((p) => p.classList.remove("drag-over"));
        document.querySelectorAll(".grid-column").forEach((c) => c.classList.remove("column-drop-target"));
        this.recordOrder();
      });

      panel.addEventListener("dragover", (e) => {
        e.preventDefault();
        e.dataTransfer.dropEffect = "move";
        if (this.draggedPanel && this.draggedPanel !== panel) {
          panel.classList.add("drag-over");
        }
      });

      panel.addEventListener("dragleave", () => {
        panel.classList.remove("drag-over");
      });

      panel.addEventListener("drop", (e) => {
        e.preventDefault();
        panel.classList.remove("drag-over");

        if (!this.draggedPanel || this.draggedPanel === panel) return;

        const targetParent = panel.parentElement;
        const rect = panel.getBoundingClientRect();
        const dropMiddleY = rect.top + rect.height / 2;

        if (e.clientY < dropMiddleY) {
          targetParent.insertBefore(this.draggedPanel, panel);
        } else {
          targetParent.insertBefore(this.draggedPanel, panel.nextSibling);
        }

        this.recordOrder();
      });
    });

    columns.forEach((col) => {
      col.addEventListener("dragover", (e) => {
        e.preventDefault();
        col.classList.add("column-drop-target");
      });
      col.addEventListener("dragleave", () => {
        col.classList.remove("column-drop-target");
      });
      col.addEventListener("drop", (e) => {
        col.classList.remove("column-drop-target");
        if (this.draggedPanel && !col.contains(this.draggedPanel)) {
          col.appendChild(this.draggedPanel);
          this.recordOrder();
        }
      });
    });
  }

  recordOrder() {
    const orderMap = {};
    const cols = document.querySelectorAll(".grid-column");
    cols.forEach((col, colIdx) => {
      const colPanels = col.querySelectorAll(".hud-panel");
      colPanels.forEach((p, pIdx) => {
        const id = p.dataset.widgetId;
        if (id) {
          orderMap[id] = { col: colIdx, pos: pIdx };
        }
      });
    });

    this.config.layoutOrder = orderMap;
    this.saveConfig();
  }

  applyConfig() {
    const panels = document.querySelectorAll(".hud-panel");
    const cols = document.querySelectorAll(".grid-column");

    // 1. Aplica minimizados e ocultos
    panels.forEach((panel) => {
      const id = panel.dataset.widgetId;
      const c = this.config[id];
      if (c) {
        if (c.hidden) panel.style.display = "none";
        if (c.minimized) panel.classList.add("minimized");
      }
    });

    // 2. Aplica reordenação se salva
    const order = this.config.layoutOrder;
    if (order && cols.length > 0) {
      const items = [];
      panels.forEach((p) => {
        const o = order[p.dataset.widgetId];
        if (o) {
          items.push({ el: p, col: o.col, pos: o.pos });
        }
      });

      items.sort((a, b) => a.pos - b.pos);
      items.forEach((item) => {
        const colEl = cols[item.col];
        if (colEl) {
          colEl.appendChild(item.el);
        }
      });
    }
  }

  initCustomizerDropdown() {
    const triggerBtn = document.getElementById("btn-custom-grid");
    const drawer = document.getElementById("grid-customizer-drawer");
    if (!triggerBtn || !drawer) return;

    triggerBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      drawer.classList.toggle("open");
    });

    document.addEventListener("click", (e) => {
      if (!drawer.contains(e.target) && e.target !== triggerBtn) {
        drawer.classList.remove("open");
      }
    });

    // Reset layout
    const resetBtn = document.getElementById("btn-reset-grid-layout");
    if (resetBtn) {
      resetBtn.addEventListener("click", () => {
        localStorage.removeItem(STORAGE_KEY);
        window.location.reload();
      });
    }

    this.syncCustomizerUI();
  }

  syncCustomizerUI() {
    const list = document.getElementById("customizer-widget-list");
    if (!list) return;

    list.innerHTML = "";
    const panels = document.querySelectorAll(".hud-panel");

    panels.forEach((p) => {
      const headerTitle = p.querySelector("header span:not(.tag)")?.textContent || p.dataset.widgetId;
      const id = p.dataset.widgetId;
      const isVisible = p.style.display !== "none";

      const item = document.createElement("label");
      item.className = "customizer-item";
      item.innerHTML = `
        <input type="checkbox" data-id="${id}" ${isVisible ? "checked" : ""}>
        <span>${headerTitle}</span>
      `;

      item.querySelector("input").addEventListener("change", (e) => {
        const checked = e.target.checked;
        p.style.display = checked ? "" : "none";
        this.config[id] = this.config[id] || {};
        this.config[id].hidden = !checked;
        this.saveConfig();
      });

      list.appendChild(item);
    });
  }
}
