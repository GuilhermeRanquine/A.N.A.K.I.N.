/**
 * A.N.A.K.I.N 3D Holographic Tactical Display (hologram-3d.js)
 * Renderizador 3D interativo 360° com Three.js (com fallback automático em Canvas 2D)
 * 1. Sede Freguesia UPGrade TI (Prédio 3D Holográfico com letreiro flutuante).
 * 2. Globo Terrestre Holográfico (Malha planetária com coordenadas de Freguesia).
 * 3. Animação de Hiper-Zoom espacial suave de transição entre Mundo e Empresa.
 */

const FREGUESIA_LAT = -22.9329;
const FREGUESIA_LON = -43.3428;

export class Hologram3DViewer {
  constructor(containerId) {
    this.container = document.getElementById(containerId);
    if (!this.container) {
      console.warn("Container #hologram-viewport não encontrado.");
      return;
    }

    this.mode = "building"; // 'building' | 'globe'
    this.isTransitioning = false;
    this.transitionStartTime = 0;
    this.transitionDuration = 1.35; // segundos

    this.autoRotate = true;
    this.lastInteractionTime = performance.now();

    // Estado esférico da câmera (360° orbit)
    this.spherical = {
      radius: 46,
      theta: Math.PI / 4,
      phi: Math.PI / 3,
    };
    this.minRadius = 18;
    this.maxRadius = 110;

    this.isDragging = false;
    this.prevPointer = { x: 0, y: 0 };

    this.accentColor = this.getAccentColor();

    if (typeof THREE !== "undefined") {
      this.initThree();
      this.buildHoloBuilding();
      this.buildHoloGlobe();
      this.initEvents();
      this.animate();
    } else {
      console.warn("Three.js não detectado no escopo global. Ativando renderizador tático Canvas 2D.");
      this.initCanvasFallback();
    }
  }

  getAccentColor() {
    const rootStyle = getComputedStyle(document.documentElement);
    const hex = rootStyle.getPropertyValue("--accent").trim();
    return hex ? parseInt(hex.replace("#", "0x"), 16) : 0xff9a3c;
  }

  getAccentHexString() {
    const rootStyle = getComputedStyle(document.documentElement);
    return rootStyle.getPropertyValue("--accent").trim() || "#ff9a3c";
  }

  // =========================================================================
  // INICIALIZAÇÃO THREE.JS
  // =========================================================================
  initThree() {
    const width = Math.max(280, this.container.clientWidth || 380);
    const height = Math.max(240, this.container.clientHeight || 270);

    this.scene = new THREE.Scene();

    this.camera = new THREE.PerspectiveCamera(45, width / height, 0.1, 1000);
    this.updateCameraPosition();

    try {
      this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    } catch (e) {
      console.warn("Falha ao inicializar WebGLRenderer. Ativando fallback.", e);
      this.initCanvasFallback();
      return;
    }

    this.renderer.setSize(width, height);
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.renderer.setClearColor(0x000000, 0);

    this.container.innerHTML = "";
    this.container.appendChild(this.renderer.domElement);

    // Iluminação
    this.ambientLight = new THREE.AmbientLight(0xffffff, 0.7);
    this.scene.add(this.ambientLight);

    this.pointLight = new THREE.PointLight(this.accentColor, 2.5, 120);
    this.pointLight.position.set(0, 30, 20);
    this.scene.add(this.pointLight);

    this.buildingGroup = new THREE.Group();
    this.globeGroup = new THREE.Group();

    this.scene.add(this.buildingGroup);
    this.scene.add(this.globeGroup);

    // Início: Prédio visível, Globo oculto
    this.globeGroup.visible = false;
    this.globeGroup.scale.set(0.001, 0.001, 0.001);

    this.resizeObserver = new ResizeObserver(() => this.onResize());
    this.resizeObserver.observe(this.container);
  }

  updateCameraPosition() {
    if (!this.camera) return;
    const r = this.spherical.radius;
    const phi = Math.max(0.1, Math.min(Math.PI / 2 - 0.05, this.spherical.phi));
    const theta = this.spherical.theta;

    this.camera.position.x = r * Math.sin(phi) * Math.sin(theta);
    this.camera.position.y = r * Math.cos(phi);
    this.camera.position.z = r * Math.sin(phi) * Math.cos(theta);

    const target = this.mode === "building" ? new THREE.Vector3(0, 9, 0) : new THREE.Vector3(0, 0, 0);
    this.camera.lookAt(target);
  }

  onResize() {
    if (!this.renderer || !this.camera || !this.container) return;
    const w = this.container.clientWidth;
    const h = this.container.clientHeight;
    if (w < 10 || h < 10) return;
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(w, h);
  }

  // =========================================================================
  // 1. HOLOGRAMA DO PRÉDIO ("Freguesia, UPGrade TI")
  // =========================================================================
  buildHoloBuilding() {
    const color = this.accentColor;
    const lineMat = new THREE.LineBasicMaterial({
      color: color,
      transparent: true,
      opacity: 0.9,
    });
    const meshMat = new THREE.MeshBasicMaterial({
      color: color,
      wireframe: true,
      transparent: true,
      opacity: 0.35,
    });

    // Grade de solo tática
    const gridHelper = new THREE.PolarGridHelper(24, 8, 8, 32, color, 0x334155);
    gridHelper.position.y = -0.1;
    this.buildingGroup.add(gridHelper);

    // Torre Principal (10 andares)
    const towerGeo = new THREE.BoxGeometry(10, 18, 10);
    const towerMesh = new THREE.Mesh(towerGeo, meshMat);
    towerMesh.position.y = 9;
    this.buildingGroup.add(towerMesh);

    const towerEdges = new THREE.EdgesGeometry(towerGeo);
    const towerLines = new THREE.LineSegments(towerEdges, lineMat);
    towerLines.position.y = 9;
    this.buildingGroup.add(towerLines);

    // Lajes intermediárias iluminadas
    for (let i = 1; i <= 9; i++) {
      const floorGeo = new THREE.PlaneGeometry(9.8, 9.8);
      const floorMat = new THREE.MeshBasicMaterial({
        color: color,
        wireframe: true,
        transparent: true,
        opacity: 0.22,
      });
      const floor = new THREE.Mesh(floorGeo, floorMat);
      floor.rotation.x = -Math.PI / 2;
      floor.position.y = i * 1.8;
      this.buildingGroup.add(floor);
    }

    // Anexo Tecnológico Lateral
    const annexGeo = new THREE.BoxGeometry(7, 8, 8);
    const annexMesh = new THREE.Mesh(annexGeo, meshMat);
    annexMesh.position.set(7.5, 4, 0);
    this.buildingGroup.add(annexMesh);

    const annexEdges = new THREE.EdgesGeometry(annexGeo);
    const annexLines = new THREE.LineSegments(annexEdges, lineMat);
    annexLines.position.set(7.5, 4, 0);
    this.buildingGroup.add(annexLines);

    // Mastro de Antena de Transmissão no Topo
    const mastGeo = new THREE.CylinderGeometry(0.12, 0.4, 7, 8);
    const mastMat = new THREE.MeshBasicMaterial({ color: color, wireframe: true });
    const mast = new THREE.Mesh(mastGeo, mastMat);
    mast.position.set(0, 21.5, 0);
    this.buildingGroup.add(mast);

    // Anéis pulsantes de satélite
    this.pulseRings = [];
    for (let r = 0; r < 3; r++) {
      const ringGeo = new THREE.RingGeometry(0.6 + r * 1.0, 0.75 + r * 1.0, 32);
      const ringMat = new THREE.MeshBasicMaterial({
        color: 0x38bdf8,
        transparent: true,
        opacity: 0.75 - r * 0.2,
        side: THREE.DoubleSide,
      });
      const ring = new THREE.Mesh(ringGeo, ringMat);
      ring.rotation.x = -Math.PI / 2;
      ring.position.set(0, 24 + r * 0.8, 0);
      this.buildingGroup.add(ring);
      this.pulseRings.push(ring);
    }

    // Letreiro Holográfico Flutuante ("Freguesia, UPGrade TI")
    this.buildingLabel = this.createHoloLabel("Freguesia, UPGrade TI\n[ HQ OPERACIONAL ]");
    this.buildingLabel.position.set(0, 28, 0);
    this.buildingGroup.add(this.buildingLabel);
  }

  // =========================================================================
  // 2. HOLOGRAMA DO MUNDO (GLOBO TERRESTRE)
  // =========================================================================
  buildHoloGlobe() {
    const color = this.accentColor;

    // Esfera Wireframe Planetária
    const sphereGeo = new THREE.SphereGeometry(15, 32, 24);
    const sphereMat = new THREE.MeshBasicMaterial({
      color: 0x38bdf8,
      wireframe: true,
      transparent: true,
      opacity: 0.25,
    });
    this.globeSphere = new THREE.Mesh(sphereGeo, sphereMat);
    this.globeGroup.add(this.globeSphere);

    // Anel Equatorial
    const eqGeo = new THREE.RingGeometry(15.2, 15.5, 64);
    const eqMat = new THREE.MeshBasicMaterial({
      color: color,
      transparent: true,
      opacity: 0.65,
      side: THREE.DoubleSide,
    });
    const eq = new THREE.Mesh(eqGeo, eqMat);
    eq.rotation.x = Math.PI / 2;
    this.globeGroup.add(eq);

    // Nuvem de Partículas Globais
    const count = 600;
    const pGeo = new THREE.BufferGeometry();
    const positions = new Float32Array(count * 3);

    for (let i = 0; i < count; i++) {
      const u = Math.random();
      const v = Math.random();
      const theta = u * 2.0 * Math.PI;
      const phi = Math.acos(2.0 * v - 1.0);
      const r = 15.1;

      positions[i * 3] = r * Math.sin(phi) * Math.cos(theta);
      positions[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta);
      positions[i * 3 + 2] = r * Math.cos(phi);
    }

    pGeo.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    const pMat = new THREE.PointsMaterial({
      color: color,
      size: 0.5,
      transparent: true,
      opacity: 0.8,
    });
    const points = new THREE.Points(pGeo, pMat);
    this.globeGroup.add(points);

    // Marcador Tático de Freguesia, Rio de Janeiro (-22.93°, -43.34°)
    const phi = (90 - FREGUESIA_LAT) * (Math.PI / 180);
    const theta = (FREGUESIA_LON + 180) * (Math.PI / 180);
    const r = 15.2;

    const markerX = -r * Math.sin(phi) * Math.cos(theta);
    const markerY = r * Math.cos(phi);
    const markerZ = r * Math.sin(phi) * Math.sin(theta);

    this.freguesiaMarkerPos = new THREE.Vector3(markerX, markerY, markerZ);

    const pinGeo = new THREE.CylinderGeometry(0.15, 0.45, 2.8, 8);
    const pinMat = new THREE.MeshBasicMaterial({ color: 0xff4d3d });
    const pin = new THREE.Mesh(pinGeo, pinMat);
    pin.position.copy(this.freguesiaMarkerPos);
    pin.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), this.freguesiaMarkerPos.clone().normalize());
    this.globeGroup.add(pin);

    // Radar pulsante
    const radarGeo = new THREE.RingGeometry(0.6, 0.95, 24);
    const radarMat = new THREE.MeshBasicMaterial({
      color: 0x4ade80,
      side: THREE.DoubleSide,
      transparent: true,
      opacity: 0.9,
    });
    this.globeRadarRing = new THREE.Mesh(radarGeo, radarMat);
    this.globeRadarRing.position.copy(this.freguesiaMarkerPos.clone().multiplyScalar(1.05));
    this.globeRadarRing.quaternion.copy(pin.quaternion);
    this.globeGroup.add(this.globeRadarRing);

    // Rótulo 3D no Globo
    this.globeLabel = this.createHoloLabel("Freguesia // UPGrade TI\n[ -22.93° / -43.34° ]");
    this.globeLabel.position.copy(this.freguesiaMarkerPos.clone().multiplyScalar(1.35));
    this.globeGroup.add(this.globeLabel);
  }

  // Criação de Text Sprite Holográfico sem dependência de roundRect
  createHoloLabel(text) {
    const canvas = document.createElement("canvas");
    canvas.width = 512;
    canvas.height = 160;
    const ctx = canvas.getContext("2d");

    // Fundo semitransparente com borda
    ctx.fillStyle = "rgba(10, 10, 18, 0.82)";
    ctx.strokeStyle = this.getAccentHexString();
    ctx.lineWidth = 4;

    if (typeof ctx.roundRect === "function") {
      ctx.beginPath();
      ctx.roundRect(10, 10, 492, 140, 10);
      ctx.fill();
      ctx.stroke();
    } else {
      ctx.fillRect(10, 10, 492, 140);
      ctx.strokeRect(10, 10, 492, 140);
    }

    // Texto de alta nitidez
    ctx.font = "bold 30px monospace";
    ctx.fillStyle = this.getAccentHexString();
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";

    const lines = text.split("\n");
    if (lines.length > 1) {
      ctx.fillText(lines[0], 256, 58);
      ctx.font = "20px monospace";
      ctx.fillStyle = "#38bdf8";
      ctx.fillText(lines[1], 256, 105);
    } else {
      ctx.fillText(text, 256, 80);
    }

    const texture = new THREE.CanvasTexture(canvas);
    const spriteMat = new THREE.SpriteMaterial({
      map: texture,
      transparent: true,
      depthTest: false,
    });
    const sprite = new THREE.Sprite(spriteMat);
    sprite.scale.set(16, 5, 1);
    return sprite;
  }

  // =========================================================================
  // 3. ANIMAÇÃO DE TRANSIÇÃO SUAVE (MUNDO <-> EMPRESA)
  // =========================================================================
  setMode(newMode) {
    if (this.mode === newMode || this.isTransitioning) return;
    this.mode = newMode;
    this.isTransitioning = true;
    this.transitionStartTime = performance.now();

    document.querySelectorAll(".holo-mode-btn").forEach((btn) => {
      btn.classList.toggle("on", btn.dataset.mode === newMode);
    });

    const indicator = document.getElementById("holo-mode-indicator");
    if (indicator) {
      indicator.textContent = newMode === "building" ? "SEDE FREGUESIA" : "GLOBO TERRESTRE";
    }

    this.buildingGroup.visible = true;
    this.globeGroup.visible = true;
  }

  // =========================================================================
  // 4. CONTROLES E EVENTOS DE ROTAÇÃO 360°
  // =========================================================================
  initEvents() {
    const el = this.renderer.domElement;

    el.addEventListener("pointerdown", (e) => {
      this.isDragging = true;
      this.prevPointer = { x: e.clientX, y: e.clientY };
      this.lastInteractionTime = performance.now();
      el.style.cursor = "grabbing";
    });

    window.addEventListener("pointermove", (e) => {
      if (!this.isDragging) return;
      const dx = e.clientX - this.prevPointer.x;
      const dy = e.clientY - this.prevPointer.y;
      this.prevPointer = { x: e.clientX, y: e.clientY };

      const rotSpeed = 0.0075;
      this.spherical.theta -= dx * rotSpeed;
      this.spherical.phi -= dy * rotSpeed;
      this.lastInteractionTime = performance.now();
    });

    window.addEventListener("pointerup", () => {
      if (this.isDragging) {
        this.isDragging = false;
        el.style.cursor = "grab";
      }
    });

    el.addEventListener(
      "wheel",
      (e) => {
        e.preventDefault();
        const zoomDelta = e.deltaY * 0.035;
        this.spherical.radius = Math.max(
          this.minRadius,
          Math.min(this.maxRadius, this.spherical.radius + zoomDelta)
        );
        this.lastInteractionTime = performance.now();
      },
      { passive: false }
    );

    el.style.cursor = "grab";
  }

  // =========================================================================
  // 5. LOOP DE RENDERIZAÇÃO THREE.JS
  // =========================================================================
  animate() {
    requestAnimationFrame(() => this.animate());

    const now = performance.now();

    // Pulso dos anéis no topo do prédio
    if (this.pulseRings) {
      const p = now * 0.003;
      this.pulseRings.forEach((ring, idx) => {
        const s = 1 + Math.sin(p + idx) * 0.12;
        ring.scale.set(s, s, 1);
      });
    }

    // Pulso do radar sobre Freguesia
    if (this.globeRadarRing) {
      const s = 1 + ((now * 0.002) % 1) * 0.85;
      this.globeRadarRing.scale.set(s, s, 1);
    }

    // Transição Suave (Hiper-Zoom)
    if (this.isTransitioning) {
      const elapsed = (now - this.transitionStartTime) / 1000;
      const t = Math.min(1.0, elapsed / this.transitionDuration);
      const ease = t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;

      if (this.mode === "building") {
        // Transição GLOBO -> PRÉDIO (Zoom In)
        this.spherical.radius = 80 - ease * (80 - 46);
        this.spherical.phi = 0.5 * Math.PI - ease * (0.5 * Math.PI - Math.PI / 3);

        const globeScale = Math.max(0.001, 1 - ease);
        this.globeGroup.scale.set(globeScale, globeScale, globeScale);

        const buildingScale = Math.min(1.0, ease * 1.05);
        this.buildingGroup.scale.set(buildingScale, buildingScale, buildingScale);

        if (t >= 1.0) {
          this.isTransitioning = false;
          this.globeGroup.visible = false;
          this.buildingGroup.scale.set(1, 1, 1);
        }
      } else {
        // Transição PRÉDIO -> GLOBO (Zoom Out)
        this.spherical.radius = 46 + ease * (76 - 46);
        this.spherical.phi = Math.PI / 3 + ease * (Math.PI / 2 - Math.PI / 3);

        const buildingScale = Math.max(0.001, 1 - ease);
        this.buildingGroup.scale.set(buildingScale, buildingScale, buildingScale);

        const globeScale = Math.min(1.0, ease);
        this.globeGroup.scale.set(globeScale, globeScale, globeScale);

        if (t >= 1.0) {
          this.isTransitioning = false;
          this.buildingGroup.visible = false;
          this.globeGroup.scale.set(1, 1, 1);
        }
      }
    } else {
      // Auto-rotação sutil quando ocioso
      if (this.autoRotate && !this.isDragging && now - this.lastInteractionTime > 1600) {
        if (this.mode === "building") {
          this.buildingGroup.rotation.y += 0.0035;
        } else {
          this.globeGroup.rotation.y += 0.0055;
        }
      }
    }

    this.updateCameraPosition();
    this.renderer.render(this.scene, this.camera);
  }

  // =========================================================================
  // 6. FALLBACK TÁTICO CANVAS 2D (caso WebGL falhe)
  // =========================================================================
  initCanvasFallback() {
    this.container.innerHTML = "";
    const canvas = document.createElement("canvas");
    canvas.width = this.container.clientWidth || 380;
    canvas.height = this.container.clientHeight || 270;
    this.container.appendChild(canvas);

    const ctx = canvas.getContext("2d");
    let angle = 0;

    const render = () => {
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      const cx = canvas.width / 2;
      const cy = canvas.height / 2;
      const accent = this.getAccentHexString();

      ctx.save();
      ctx.translate(cx, cy);

      if (this.mode === "building") {
        // Prédio isométrico em wireframe
        ctx.strokeStyle = accent;
        ctx.lineWidth = 1.5;
        const w = 60, h = 120, d = 60;
        const cos = Math.cos(angle);
        const sin = Math.sin(angle);

        ctx.strokeRect(-w / 2 * cos, -h / 2, w * cos, h);
        ctx.strokeRect(-w / 2 * sin, -h / 2 + 20, w * sin, h - 20);

        // Letreiro 2D
        ctx.fillStyle = accent;
        ctx.font = "bold 13px monospace";
        ctx.textAlign = "center";
        ctx.fillText("Freguesia, UPGrade TI", 0, -h / 2 - 18);
        ctx.fillStyle = "#38bdf8";
        ctx.font = "9px monospace";
        ctx.fillText("[ HQ OPERACIONAL ]", 0, -h / 2 - 4);
      } else {
        // Globo 2D
        ctx.strokeStyle = "#38bdf8";
        ctx.beginPath();
        ctx.arc(0, 0, 70, 0, Math.PI * 2);
        ctx.stroke();

        ctx.strokeStyle = accent;
        ctx.beginPath();
        ctx.ellipse(0, 0, 70, 25, angle, 0, Math.PI * 2);
        ctx.stroke();

        ctx.fillStyle = "#ff4d3d";
        ctx.beginPath();
        ctx.arc(20 * Math.cos(angle), 15 * Math.sin(angle), 5, 0, Math.PI * 2);
        ctx.fill();

        ctx.fillStyle = accent;
        ctx.font = "bold 11px monospace";
        ctx.textAlign = "center";
        ctx.fillText("Freguesia // UPGrade TI", 0, -85);
      }

      ctx.restore();
      angle += 0.015;
      requestAnimationFrame(render);
    };

    render();
  }
}
