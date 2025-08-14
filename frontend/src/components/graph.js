// graph.js – optimized 3D spheres + click-to-identify (node.id)
import ForceGraph3D from '3d-force-graph';
import * as THREE from 'three';

const BACKEND_BASE = 'http://127.0.0.1:8000';

// ---------- tweakables (optimized for performance) --------------------
const EDGE_CAP   = 50000;    // reduced from 100000
const SEG_CAP    = 2000000;  // reduced from 5000000

const SPHERE_RADIUS = 6;
const EDGE_COLOR    = 0x888888;
const EDGE_OPACITY  = 0.20;

/* Palette for the first 20 communities; others fall back to grey */
const PALETTE = [
  0x1f77b4, 0xff7f0e, 0x2ca02c, 0xd62728, 0x9467bd,
  0x8c564b, 0xe377c2, 0x7f7f7f, 0xbcbd22, 0x17becf,
  0xe6194b, 0x3cb44b, 0x4363d8, 0xf58231, 0x911eb4,
  0x46f0f0, 0xf032e6, 0xbcf60c, 0xfabebe, 0x008080
];
const FALLBACK_COLOR = 0x444444;
const TOP_K = 20;

// Performance settings
const LOD_DISTANCES = { high: 0, medium: 1000, low: 5000 };
const EDGE_VISIBILITY_DISTANCE = 15000;

// Picking/tooltip globals
const DISPLAY_FIELD = 'name';
let PICKABLE_MESHES = [];
let ACTIVE_HIT = null;
let TOOLTIP_EL = null;

document.addEventListener('DOMContentLoaded', () => {
  const el = document.getElementById('3d-graph');
  if (!el) return;

  // -----------  create ForceGraph3D instance (optimized)  --------------
  const fg = ForceGraph3D({
    rendererConfig: {
      antialias: false,
      powerPreference: 'high-performance',
      logarithmicDepthBuffer: false,
      precision: 'mediump'
    }
  })(el)
    .graphData({ nodes: [], links: [] })
    .d3Force('charge', null)
    .cooldownTicks(0)
    .enableNodeDrag(false)
    .linkVisibility(() => false); // we draw edges manually

  // lighting
  fg.scene().add(new THREE.AmbientLight(0xffffff, 0.7));
  const dirLight = new THREE.DirectionalLight(0xffffff, 0.5);
  dirLight.position.set(1, 1, 1).normalize();
  fg.scene().add(dirLight);

  // camera / controls tweaks
  const cam = fg.camera();
  cam.near = 0.1;
  cam.far  = 1e9;
  cam.updateProjectionMatrix();

  const controls = fg.controls();
  Object.assign(controls, {
    screenSpacePanning: false,
    minDistance: 100,
    maxDistance: 1_000_000,
    maxPolarAngle: Math.PI / 2,
    enableDamping: true,
    dampingFactor: 0.05
  });
  if (controls.constraint) {
    controls.constraint.smoothZoom         = true;
    controls.constraint.zoomDampingFactor  = 0.2;
    controls.constraint.smoothZoomSpeed    = 5.0;
  }

  let lastControlUpdate = 0;
  const CONTROL_UPDATE_INTERVAL = 16; // ~60fps
  controls.addEventListener('change', () => {
    const now = performance.now();
    if (now - lastControlUpdate > CONTROL_UPDATE_INTERVAL) {
      lastControlUpdate = now;
      if (ACTIVE_HIT) updateTooltipPosition(fg, cam);
    }
  });

  ensureTooltip(el);

  // ---------------- fetch graph data -----------------------------------
  const url = `${BACKEND_BASE}/data/graph?_cb=${Date.now()}`;
  fetch(url)
    .then(r => r.json())
    .then(data => {
      let { nodes, links } = data;
      if (EDGE_CAP != null && links.length > EDGE_CAP) {
        links = links.slice(0, EDGE_CAP);
      }

      // lock node positions – ForceGraph3D will respect fx/y/z
      nodes.forEach(n => {
        n.fx = n.x;
        n.fy = n.y;
        n.fz = n.z ?? 0;
      });

      // determine the TOP_K largest communities
      const counts = {};
      nodes.forEach(n => {
        const id = +n.community;
        counts[id] = (counts[id] || 0) + 1;
      });
      const topCommunities = Object.entries(counts)
        .sort((a, b) => b[1] - a[1])
        .slice(0, TOP_K)
        .map(([id]) => +id);

      // optimized node rendering with instancing (+ picking metadata)
      setupInstancedNodes(fg, nodes, topCommunities);

      fg.graphData({ nodes, links });
      addOptimizedBundledEdges(fg.scene(), links);

      // camera centring
      const { center, diag } = computeBBox(nodes);
      controls.target.set(center.x, center.y, center.z);
      cam.position.set(center.x, center.y, center.z + diag * 0.8);
      cam.updateProjectionMatrix();

      // picking: click-to-identify
      installPicking(fg, cam);
    })
    .catch(console.error);
});

// ---------- optimized node rendering with instancing + picking --------
function setupInstancedNodes(fg, nodes, topCommunities) {
  const sphereGeoHigh = new THREE.SphereGeometry(SPHERE_RADIUS, 8, 8);
  const sphereGeoMed  = new THREE.SphereGeometry(SPHERE_RADIUS, 6, 6);
  const sphereGeoLow  = new THREE.SphereGeometry(SPHERE_RADIUS, 4, 4);

  const nodesByColor = {};
  nodes.forEach(node => {
    const comm = +node.community;
    const idx = topCommunities.indexOf(comm);
    const color = idx >= 0 ? PALETTE[idx % PALETTE.length] : FALLBACK_COLOR;
    (nodesByColor[color] ||= []).push(node);
  });

  Object.entries(nodesByColor).forEach(([color, colorNodes]) => {
    const material = new THREE.MeshBasicMaterial({ color: +color });

    const lod = new THREE.LOD();
    const instancedHigh = new THREE.InstancedMesh(sphereGeoHigh, material, colorNodes.length);
    const instancedMed  = new THREE.InstancedMesh(sphereGeoMed , material, colorNodes.length);
    const instancedLow  = new THREE.InstancedMesh(sphereGeoLow , material, colorNodes.length);

    const m = new THREE.Matrix4();
    colorNodes.forEach((node, i) => {
      m.setPosition(node.x, node.y, node.z || 0);
      instancedHigh.setMatrixAt(i, m);
      instancedMed .setMatrixAt(i, m);
      instancedLow .setMatrixAt(i, m);
    });
    instancedHigh.instanceMatrix.needsUpdate = true;
    instancedMed .instanceMatrix.needsUpdate = true;
    instancedLow .instanceMatrix.needsUpdate = true;

    // map instanceId → node for picking
    instancedHigh.userData.nodes = colorNodes;
    instancedMed .userData.nodes = colorNodes;
    instancedLow .userData.nodes = colorNodes;

    // register for raycasting
    PICKABLE_MESHES.push(instancedHigh, instancedMed, instancedLow);

    lod.addLevel(instancedHigh, LOD_DISTANCES.high);
    lod.addLevel(instancedMed , LOD_DISTANCES.medium);
    lod.addLevel(instancedLow , LOD_DISTANCES.low);

    fg.scene().add(lod);
  });

  // Hide default ForceGraph3D node rendering
  fg.nodeThreeObject(() => new THREE.Object3D());
}

// ---------- optimized edge bundling with distance culling -------------
function addOptimizedBundledEdges(scene, links) {
  let segCount = 0;
  for (const e of links) {
    const pts = parsePoints(e.points);
    if (pts && pts.length > 1) segCount += pts.length - 1;
    if (SEG_CAP != null && segCount >= SEG_CAP) break;
  }

  const positions = new Float32Array(segCount * 2 * 3);
  let w = 0;
  for (const e of links) {
    const pts = parsePoints(e.points);
    if (!pts || pts.length < 2) continue;
    for (let i = 0; i < pts.length - 1; i++) {
      if (SEG_CAP != null && (w / 3) >= SEG_CAP * 2) break;
      const a = pts[i], b = pts[i + 1];
      positions[w]     = a.x; positions[w + 1] = a.y; positions[w + 2] = a.z;
      positions[w + 3] = b.x; positions[w + 4] = b.y; positions[w + 5] = b.z;
      w += 6;
    }
    if (SEG_CAP != null && (w / 3) >= SEG_CAP * 2) break;
  }

  const geom = new THREE.BufferGeometry();
  geom.setAttribute('position',
    new THREE.BufferAttribute(
      w === positions.length ? positions : positions.slice(0, w), 3
    ));
  geom.computeBoundingSphere();

  const mat = new THREE.LineBasicMaterial({
    color: EDGE_COLOR,
    transparent: true,
    opacity: EDGE_OPACITY,
    vertexColors: false
  });

  const lines = new THREE.LineSegments(geom, mat);
  lines.frustumCulled = true;
  lines.userData.__graphObject = true;

  // Optional distance-based visibility
  // lines.onBeforeRender = function(renderer, scene, camera) {
  //   const s = this.geometry.boundingSphere;
  //   if (s) {
  //     const d = camera.position.distanceTo(s.center);
  //     this.visible = d < EDGE_VISIBILITY_DISTANCE;
  //   }
  // };

  scene.add(lines);
}

// --------------------- picking / tooltip ------------------------------
function installPicking(fg, cam) {
  const canvas = fg.renderer().domElement;
  const raycaster = new THREE.Raycaster();
  const mouse = new THREE.Vector2();

  // Slight tolerance for low-segment spheres
  raycaster.params.Mesh = { ...(raycaster.params.Mesh || {}), threshold: SPHERE_RADIUS * 0.4 };

  canvas.addEventListener('pointerdown', (evt) => {
    screenToNDC(evt, canvas, mouse);
    raycaster.setFromCamera(mouse, cam);
    const hits = raycaster.intersectObjects(PICKABLE_MESHES, false);

    if (!hits.length) {
      hideTooltip();
      return;
    }
    const hit = hits[0];
    const arr = hit.object.userData?.nodes;
    const node = (arr && hit.instanceId != null) ? arr[hit.instanceId] : null;

    if (node) {
      showTooltip(node, hit.point, fg, cam);
    } else {
      hideTooltip();
    }
  });
}

function ensureTooltip(containerEl) {
  if (TOOLTIP_EL) return;
  TOOLTIP_EL = document.createElement('div');
  Object.assign(TOOLTIP_EL.style, {
    position: 'absolute',
    pointerEvents: 'none',
    transform: 'translate(-50%, -120%)',
    padding: '6px 8px',
    font: '12px/1.2 system-ui, sans-serif',
    background: 'rgba(0,0,0,0.75)',
    color: '#fff',
    borderRadius: '6px',
    whiteSpace: 'nowrap',
    display: 'none',
    zIndex: 10
  });
  containerEl.appendChild(TOOLTIP_EL);
}

function screenToNDC(evt, canvas, outVec2) {
  const rect = canvas.getBoundingClientRect();
  const x = ((evt.clientX - rect.left) / rect.width) * 2 - 1;
  const y = -((evt.clientY - rect.top) / rect.height) * 2 + 1;
  outVec2.set(x, y);
}

function showTooltip(node, point, fg, cam) {
  ACTIVE_HIT = { node, point: point.clone() };
  const text = node?.[DISPLAY_FIELD] ?? node?.name ?? node?.name ?? '(unknown)';
  TOOLTIP_EL.textContent = String(text);
  TOOLTIP_EL.style.display = 'block';
  updateTooltipPosition(fg, cam);
}

function hideTooltip() {
  ACTIVE_HIT = null;
  if (TOOLTIP_EL) TOOLTIP_EL.style.display = 'none';
}

function updateTooltipPosition(fg, cam) {
  if (!ACTIVE_HIT || !TOOLTIP_EL) return;
  const canvas = fg.renderer().domElement;
  const p = ACTIVE_HIT.point.clone().project(cam);
  const halfW = canvas.clientWidth / 2;
  const halfH = canvas.clientHeight / 2;
  const sx = (p.x * halfW) + halfW;
  const sy = (-p.y * halfH) + halfH;
  TOOLTIP_EL.style.left = `${sx}px`;
  TOOLTIP_EL.style.top  = `${sy}px`;
}

// --------------------- helpers ----------------------------------------
function parsePoints(p) {
  if (!p) return null;
  if (Array.isArray(p)) return p.map(o => ({ x: +o.x, y: +o.y, z: +o.z }));
  if (typeof p === 'string') {
    return p.split('|').map(str => {
      const [xs, ys, zs] = str.split(',');
      return { x: +xs, y: +ys, z: +zs };
    });
  }
  return null;
}

function computeBBox(nodes) {
  let minX =  Infinity, maxX = -Infinity,
      minY =  Infinity, maxY = -Infinity,
      minZ =  Infinity, maxZ = -Infinity;

  nodes.forEach(n => {
    if (n.x < minX) minX = n.x;
    if (n.x > maxX) maxX = n.x;
    if (n.y < minY) minY = n.y;
    if (n.y > maxY) maxY = n.y;
    if (n.z < minZ) minZ = n.z;
    if (n.z > maxZ) maxZ = n.z;
  });

  const center = {
    x: (minX + maxX) / 2,
    y: (minY + maxY) / 2,
    z: (minZ + maxZ) / 2
  };
  const dx = maxX - minX, dy = maxY - minY, dz = maxZ - minZ;
  const diag = Math.sqrt(dx * dx + dy * dy + dz * dz);
  return { center, diag };
}

/* ---------- optional: performance monitoring --------------------------
import Stats from 'stats.js';
const stats = new Stats();
stats.showPanel(0);
document.body.appendChild(stats.dom);
function animate() {
  stats.begin();
  stats.end();
  requestAnimationFrame(animate);
}
animate();
----------------------------------------------------------------------- */
