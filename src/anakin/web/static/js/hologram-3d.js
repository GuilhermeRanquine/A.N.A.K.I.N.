/**
 * A.N.A.K.I.N 3D Holographic Tactical Display (hologram-3d.js)
 * Renderizador WebGL 3D em Three.js para o Holograma Interativo 360°:
 * 1. Sede UPGrade TI (Prédio Holográfico em Freguesia com letreiro flutuante).
 * 2. Globo Terrestre Holográfico (Malha planetária com marcador de coordenadas).
 * 3. Transição cinematográfica suave com efeito de Hiper-Zoom espacial.
 */

// Constantes táticas de Freguesia, Rio de Janeiro
const FREGUESIA_LAT = -22.9329;
const FREGUESIA_LON = -43.3428;

export class Hologram3DViewer {
  constructor(containerId) {
    this.container = document.getElementById(containerId);
    if (!this.container) return;

    this.mode = "building"; // 'building' | 'globe'
    this.isTransitioning = false;
    this.transitionProgress = 0;
    this.transitionDuration = 1.35; // segundos

    this.autoRotate = true;
    this.lastInteractionTime = performance.now();

    // Estado da câmera e rotação interativa 360°
    this.spherical = {
      radius: 46,
      theta: Math.PI / 4,
      phi: Math.PI / 3,
    };
    this.targetRadius = 46;
    this.minRadius = 18;
    this.maxRadius = 110;

    this.isDragging = false;
    this.prevPointer = { x: 0, y: 0 };

    this.accentColor = this.getAccentColor();

    this.initThree();
    this.buildHoloBuilding();
    this.buildHoloGlobe();
    this.initEvents();
    this.animate();
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

  initThree() {
    if (typeof THREE === "undefined") {
      console.error("Three.js não carregado. Holograma desativado.");
      return;
    }

    const width = this.container.clientWidth || 380;
    const height = this.container.clientHeight || 260;

    // Cena
    this.scene = new THREE.Scene();

    // Câmera perspectiva
    this.camera = new THREE.PerspectiveCamera(45, width / height, 0.1, 1000);
    this.updateCameraPosition();

    // Renderizador WebGL
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    this.renderer.setSize(width, height);
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.renderer.setClearColor(0x000000, 0);

    this.container.innerHTML = "";
    this.container.appendChild(this.renderer.domElement);

    // Iluminação Holográfica
    this.ambientLight = new THREE.AmbientLight(0xffffff, 0.6);
    this.scene.add(this.ambientLight);

    this.pointLight = new THREE.PointLight(this.accentColor, 2.5, 120);
    this.pointLight.position.set(0, 30, 20);
    this.scene.add(this.pointLight);

    // Grupo Raiz
    this.buildingGroup = new THREE.Group();
    this.globeGroup = new THREE.Group();

    this.scene.add(this.buildingGroup);
    this.scene.add(this.globeGroup);

    // Visibilidade inicial: Prédio ativo, Globo oculto
    this.globeGroup.visible = false;
    this.globeGroup.scale.set(0.001, 0.001, 0.001);

    // ResizeObserver para responsividade do widget
    this.resizeObserver = new ResizeObserver(() => this.onResize());
    this.resizeObserver.observe(this.container);
  }

  updateCameraPosition() {
    // Converte esférico para cartesiano com suavização
    const r = this.spherical.radius;
    const phi = Math.max(0.1, Math.min(Math.PI / 2 - 0.05, this.spherical.phi));
    const theta = this.spherical.theta;

    this.camera.position.x = r * Math.sin(phi) * Math.sin(theta);
    this.camera.position.y = r * Math.cos(phi);
    this.camera.position.z = r * Math.sin(phi) * Math.cos(theta);

    const lookTarget = this.mode === "building" ? new THREE.Vector3(0, 8, 0) : new THREE.Vector3(0, 0, 0);
    this.camera.lookAt(lookTarget);
  }

  onResize() {
    if (!this.renderer || !this.camera || !this.container) return;
    const w = this.container.clientWidth;
    const h = this.container.clientHeight;
    if (w === 0 || h === 0) return;
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(w, h);
  }

  // =========================================================================
  // 1. CONSTRUÇÃO DO HOLOGRAMA DO PRÉDIO ("Freguesia, UPGrade TI")
  // =========================================================================
  buildHoloBuilding() {
    const color = this.accentColor;
    const lineMat = new THREE.LineBasicMaterial({
      color: color,
      transparent: true,
      opacity: 0.85,
    });
    const meshMat = new THREE.MeshBasicMaterial({
      color: color,
      wireframe: true,
      transparent: true,
      opacity: 0.35,
    });

    // Grade holográfica de solo (Target Ground Ring)
    const gridHelper = new THREE.PolarGridHelper(24, 8, 8, 32, color, 0x334155);
    gridHelper.position.y = -0.1;
    this.buildingGroup.add(gridHelper);

    // 1. Corpo Principal do Prédio UPGrade TI (Torre de 10 andares)
    const towerGeo = new THREE.BoxGeometry(10, 18, 10);
    const towerMesh = new THREE.Mesh(towerGeo, meshMat);
    towerMesh.position.y = 9;
    this.buildingGroup.add(towerMesh);

    const towerEdges = new THREE.EdgesGeometry(towerGeo);
    const towerLines = new THREE.LineSegments(towerEdges, lineMat);
    towerLines.position.y = 9;
    this.buildingGroup.add(towerLines);

    // Lajes / Andares iluminados (10 pavimentos)
    for (let i = 1; i <= 9; i++) {
      const floorGeo = new THREE.PlaneGeometry(9.8, 9.8);
      const floorMat = new THREE.MeshBasicMaterial({
        color: color,
        wireframe: true,
        transparent: true,
        opacity: 0.25,
      });
      const floor = new THREE.Mesh(floorGeo, floorMat);
      floor.rotation.x = -Math.PI / 2;
      floor.position.y = i * 1.8;
      this.buildingGroup.add(floor);
    }

    // 2. Anexo Lateral Tecnológico (Hub de Servidores)
    const annexGeo = new THREE.BoxGeometry(7, 8, 8);
    const annexMesh = new THREE.Mesh(annexGeo, meshMat);
    annexMesh.position.set(7.5, 4, 0);
    this.buildingGroup.add(annexMesh);

    const annexEdges = new THREE.EdgesGeometry(annexGeo);
    const annexLines = new THREE.LineSegments(annexEdges, lineMat);
    annexLines.position.set(7.5, 4, 0);
    this.buildingGroup.add(annexLines);

    // 3. Torre de Antena & Transmissão no Topo do Prédio
    const mastGeo = new THREE.CylinderGeometry(0.12, 0.4, 7, 8);
    const mastMat = new THREE.MeshBasicMaterial({ color: color, wireframe: true });
    const mast = new THREE.Mesh(mastGeo, mastMat);
    mast.position.set(0, 21.5, 0);
    this.buildingGroup.add(mast);

    // Anéis pulsantes de transmissão via satélite
    this.pulseRings = [];
    for (let r = 0; r < 3; r++) {
      const ringGeo = new THREE.RingGeometry(0.5 + r * 1.0, 0.6 + r * 1.0, 32);
      const ringMat = new THREE.MeshBasicMaterial({
        color: 0x38bdf8,
        transparent: true,
        opacity: 0.7 - r * 0.2,
        side: THREE.DoubleSide,
      });
      const ring = new THREE.Mesh(ringGeo, ringMat);
      ring.rotation.x = -Math.PI / 2;
      ring.position.set(0, 24 + r * 0.8, 0);
      this.buildingGroup.add(ring);
      this.pulseRings.push(ring);
    }

    // 4. Letreiro Holográfico Flutuante ("Freguesia, UPGrade TI")
    this.buildingLabel = this.createHoloLabel("Freguesia, UPGrade TI\n[ HQ OPERACIONAL ]");
    this.buildingLabel.position.set(0, 28, 0);
    this.buildingGroup.add(this.buildingLabel);
  }

  // =========================================================================
  // 2. CONSTRUÇÃO DO HOLOGRAMA DO MUNDO (GLOBO TERRESTRE)
  // =========================================================================
  buildHoloGlobe() {
    const color = this.accentColor;

    // 1. Esfera Wireframe Planetária
    const sphereGeo = new THREE.SphereGeometry(15, 32, 24);
    const sphereMat = new THREE.MeshBasicMaterial({
      color: 0x38bdf8,
      wireframe: true,
      transparent: true,
      opacity: 0.22,
    });
    this.globeSphere = new THREE.Mesh(sphereGeo, sphereMat);
    this.globeGroup.add(this.globeSphere);

    // 2. Anéis equatoriais e meridianos de precisão
    const eqGeo = new THREE.RingGeometry(15.2, 15.4, 64);
    const eqMat = new THREE.MeshBasicMaterial({
      color: color,
      transparent: true,
      opacity: 0.65,
      side: THREE.DoubleSide,
    });
    const eq = new THREE.Mesh(eqGeo, eqMat);
    eq.rotation.x = Math.PI / 2;
    this.globeGroup.add(eq);

    // 3. Nuvem de Partículas Globais (Continentes / Nodos de Rede)
    const particleCount = 650;
    const pGeo = new THREE.BufferGeometry();
    const positions = new Float32Array(particleCount * 3);

    for (let i = 0; i < particleCount; i++) {
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
      size: 0.45,
      transparent: true,
      opacity: 0.75,
    });
    const particlePoints = new THREE.Points(pGeo, pMat);
    this.globeGroup.add(particlePoints);

    // 4. Marcador Tático de Freguesia, Rio de Janeiro (-22.93°, -43.34°)
    const phi = (90 - FREGUESIA_LAT) * (Math.PI / 180);
    const theta = (FREGUESIA_LON + 180) * (Math.PI / 180);
    const r = 15.2;

    const markerX = -r * Math.sin(phi) * Math.cos(theta);
    const markerY = r * Math.cos(phi);
    const markerZ = r * Math.sin(phi) * Math.sin(theta);

    this.freguesiaMarkerPos = new THREE.Vector3(markerX, markerY, markerZ);

    // Pino holográfico de localização
    const pinGeo = new THREE.CylinderGeometry(0.1, 0.4, 2.5, 8);
    const pinMat = new THREE.MeshBasicMaterial({ color: 0xff4d3d });
    const pin = new THREE.Mesh(pinGeo, pinMat);
    pin.position.copy(this.freguesiaMarkerPos);
    pin.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), this.freguesiaMarkerPos.clone().normalize());
    this.globeGroup.add(pin);

    // Anel de radar pulsante sobre o marcador
    const radarGeo = new THREE.RingGeometry(0.6, 0.9, 24);
    const radarMat = new THREE.MeshBasicMaterial({
      color: 0x4ade80,
      side: THREE.DoubleSide,
      transparent: true,
      opacity: 0.9,
    });
    this.globeRadarRing = new THREE.Mesh(radarGeo, radarMat);
    this.globeRadarRing.position.copy(this.freguesiaMarkerPos.clone().multiplyScalar(1.04));
    this.globeRadarRing.quaternion.copy(pin.quaternion);
    this.globeGroup.add(this.globeRadarRing);

    // Rótulo 3D sobre o Rio de Janeiro
    this.globeLabel = this.createHoloLabel("Freguesia // UPGrade TI\n[ -22.93° / -43.34° ]");
    this.globeLabel.position.copy(this.freguesiaMarkerPos.clone().multiplyScalar(1.35));
    this.globeGroup.add(this.globeLabel);
  }

  // Criação de Text Sprite Holográfico em Canvas 2D de alta nitidez
  createHoloLabel(text) {
    const canvas = document.createElement("canvas");
    canvas.width = 512;
    canvas.height = 160;
    const ctx = canvas.getContext("2d");

    ctx.fillStyle = "rgba(0, 0, 0, 0.75)";
    ctx.strokeStyle = this.getAccentHexString();
    ctx.lineWidth = 4;
    ctx.beginPath();
    ctx.roundRect(10, 10, 492, 140, 12);
    ctx.fill();
    ctx.stroke();

    // Texto
    ctx.font = "bold 32px monospace";
    ctx.fillStyle = this.getAccentHexString();
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";

    const lines = text.split("\n");
    if (lines.length > 1) {
      ctx.fillText(lines[0], 256, 60);
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
  // 3. ANIMAÇÃO SUAVE DE TRANSIÇÃO (HIPER-ZOOM: MUNDO <-> PRÉDIO)
  // =========================================================================
  setMode(newMode) {
    if (this.mode === newMode || this.isTransitioning) return;
    this.mode = newMode;
    this.isTransitioning = true;
    this.transitionStartTime = performance.now();

    // Atualiza botões da UI externa
    document.querySelectorAll(".holo-mode-btn").forEach((btn) => {
      btn.classList.toggle("on", btn.dataset.mode === newMode);
    });

    const statusBadge = document.getElementById("holo-mode-indicator");
    if (statusBadge) {
      statusBadge.textContent = newMode === "building" ? "SEDE FREGUESIA" : "GLOBO TERRESTRE";
    }

    if (newMode === "building") {
      // Transição de Globo para Prédio: Ativa ambos para o zoom cinematográfico
      this.buildingGroup.visible = true;
      this.globeGroup.visible = true;
    } else {
      // Transição de Prédio para Globo
      this.buildingGroup.visible = true;
      this.globeGroup.visible = true;
    }
  }

  // =========================================================================
  // 4. CONTROLES E EVENTOS DE ROTAÇÃO 360°
  // =========================================================================
  initEvents() {
    const el = this.renderer.domElement;

    // Pointer Drag (360° Rotate)
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

      const rotSpeed = 0.007;
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

    // Zoom com Mouse Wheel
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
  // 5. LOOP DE RENDERIZAÇÃO
  // =========================================================================
  animate() {
    requestAnimationFrame(() => this.animate());

    const now = performance.now();

    // Pulso dos anéis de satélite no topo do prédio
    if (this.pulseRings) {
      const pulseTime = now * 0.003;
      this.pulseRings.forEach((ring, idx) => {
        const scale = 1 + Math.sin(pulseTime + idx) * 0.12;
        ring.scale.set(scale, scale, 1);
      });
    }

    // Pulso do radar sobre o marcador de Freguesia no globo
    if (this.globeRadarRing) {
      const radarScale = 1 + ((now * 0.002) % 1) * 0.8;
      this.globeRadarRing.scale.set(radarScale, radarScale, 1);
    }

    // Gerenciador de Transição Cinematográfica com EaseInOut
    if (this.isTransitioning) {
      const elapsed = (now - this.transitionStartTime) / 1000;
      const t = Math.min(1.0, elapsed / this.transitionDuration);

      // Função de atenuação suave (Ease-In-Out Cubic)
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
      // Auto-rotação contínua e suave quando ocioso
      if (this.autoRotate && !this.isDragging && now - this.lastInteractionTime > 1800) {
        if (this.mode === "building") {
          this.buildingGroup.rotation.y += 0.004;
        } else {
          this.globeGroup.rotation.y += 0.006;
        }
      }
    }

    this.updateCameraPosition();
    this.renderer.render(this.scene, this.camera);
  }
}
