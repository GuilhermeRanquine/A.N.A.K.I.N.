/**
 * A.N.A.K.I.N Grid Customization & Drag-and-Drop Manager (grid-manager.js)
 * Permite movimentar janelas, personalizar visualizações, minimizar e salvar layouts.
 */

const STORAGE_KEY = "anakin_hud_grid_layout_v2";

export class HudGridManager {
  constructor() {
    this.container = document.querySelector(".dashboard-grid");
    this.columns = document.querySelectorAll(".left-column, .center-column, .right-column");
    this.draggedPanel = null;
    this.config = this.loadConfig();

    this.initPanelHandles();
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

  initPanelHandles() {
    const panels = document.querySelectorAll(".hud-panel");
    panels.forEach((panel) => {
      const header = panel.querySelector("header");
      if (!header) return;

      const widgetId = panel.dataset.widgetId || panel.id || header.innerText.trim().slice(0, 16);
      panel.dataset.widgetId = widgetId;

      // Adiciona Alça de Arraste (Grip) se ainda não tiver
      if (!header.querySelector(".hud-drag-handle")) {
        const grip = document.createElement("span");
        grip.className = "hud-drag-handle";
        grip.innerHTML = "⋮⋮";
        grip.title = "Clique e arraste para movimentar esta janela no grid";
        header.insertBefore(grip, header.firstChild);
      }

      // Adiciona Ações de Janela (Minimizar, Expandir, Fechar)
      if (!header.querySelector(".hud-window-actions")) {
        const actions = document.createElement("div");
        actions.className = "hud-window-actions";
        actions.innerHTML = `
          <button class="win-btn win-min" title="Minimizar janela">_</button>
          <button class="win-btn win-expand" title="Expandir / Restaurar largura">⤢</button>
          <button class="win-btn win-close" title="Ocultar janela">✕</button>
        `;
        header.appendChild(actions);

        // Eventos dos botões de janela
        actions.querySelector(".win-min").addEventListener("click", (e) => {
          e.stopPropagation();
          panel.classList.toggle("minimized");
          this.config[widgetId] = this.config[widgetId] || {};
          this.config[widgetId].minimized = panel.classList.contains("minimized");
          this.saveConfig();
        });

        actions.querySelector(".win-expand").addEventListener("click", (e) => {
          e.stopPropagation();
          panel.classList.toggle("expanded-full");
          this.config[widgetId] = this.config[widgetId] || {};
          this.config[widgetId].expanded = panel.classList.contains("expanded-full");
          this.saveConfig();
        });

        actions.querySelector(".win-close").addEventListener("click", (e) => {
          e.stopPropagation();
          panel.style.display = "none";
          this.config[widgetId] = this.config[widgetId] || {};
          this.config[widgetId].hidden = true;
          this.saveConfig();
          this.syncCustomizerUI();
        });
      }

      // Torna o header arrastável
      header.setAttribute("draggable", "true");
    });
  }

  initDragAndDrop() {
    const panels = document.querySelectorAll(".hud-panel");

    panels.forEach((panel) => {
      const header = panel.querySelector("header");
      if (!header) return;

      header.addEventListener("dragstart", (e) => {
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

        // Inserção relativa no DOM
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

    // Permite soltar em colunas vazias
    this.columns.forEach((col) => {
      col.addEventListener("dragover", (e) => {
        e.preventDefault();
      });
      col.addEventListener("drop", (e) => {
        if (this.draggedPanel && !col.contains(this.draggedPanel)) {
          col.appendChild(this.draggedPanel);
          this.recordOrder();
        }
      });
    });
  }

  recordOrder() {
    const orderMap = {};
    this.columns.forEach((col, colIdx) => {
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
    // 1. Aplica minimizados e ocultos
    document.querySelectorAll(".hud-panel").forEach((panel) => {
      const id = panel.dataset.widgetId;
      const c = this.config[id];
      if (c) {
        if (c.hidden) panel.style.display = "none";
        if (c.minimized) panel.classList.add("minimized");
        if (c.expanded) panel.classList.add("expanded-full");
      }
    });

    // 2. Aplica reordenação se existir
    const order = this.config.layoutOrder;
    if (order) {
      const items = [];
      document.querySelectorAll(".hud-panel").forEach((p) => {
        const o = order[p.dataset.widgetId];
        if (o) {
          items.push({ el: p, col: o.col, pos: o.pos });
        }
      });

      items.sort((a, b) => a.pos - b.pos);
      items.forEach((item) => {
        const colEl = this.columns[item.col];
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

    triggerBtn.addEventListener("click", () => {
      drawer.classList.toggle("open");
    });

    // Reset button
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
