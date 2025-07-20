// graph.js
import * as THREE from 'three';
import ForceGraph3D from '3d-force-graph';

/*
 * ================= Adjustable Client-Side Knobs =================
 */
const BACKEND_BASE = 'http://127.0.0.1:8000';   // change to '' if same-origin in prod

// Default request params (override via refreshGraph({...}))
const DEFAULT_QUERY = {
  nodes: 'all',          // 'all' | integer
  sampling: 'none',      // none | random | grid
  include_edges: 1,      // 1 | 0
  edge_factor: 2.0,      // ignored if max_edges set
  max_edges: 'all',      // 'all' | integer | omit
  degree_cap: 'none',    // 'none' | integer
  edge_mode: 'random',   // random | sequential
  max_per_cell: 10,      // only for grid
  shuffle_nodes: 1
};

// Visualization scaling
const USE_FOLLOWER_SIZE = false;  // if true, size scales with followers
const POINT_SIZE = 4.5;           // base sprite size (pixels if sizeAttenuation)
const POINT_SIZE_MIN = 2.5;
const POINT_SIZE_MAX = 10.0;

// If raw coordinates span a huge box, apply uniform scale
const AUTO_SCALE = true;
const TARGET_DIAGONAL = 4000;     // world units target for bbox diagonal after scaling

// Add slight Z jitter so cloud has volume
const Z_JITTER = 1.0;

// Edge rendering client cap to prevent GPU overload (null => no extra cap)
const CLIENT_EDGE_CAP = null;     // e.g. 150000 to clamp

// Visual styles
const EDGE_COLOR = 0x888888;
const EDGE_OPACITY = 0.20;

// Hover distance threshold squared (world units) for brute-force pick
const HOVER_RADIUS = 30;  // approximate world distance
// =================================================================

document.addEventListener('DOMContentLoaded', () => {
  const container = document.getElementById('3d-graph');
  if (!container) return;

  const fg = ForceGraph3D()(container)
    .graphData({ nodes: [], links: [] })
    .d3Force('charge', null)
    .cooldownTicks(0)
    .enableNodeDrag(false)
    .nodeVisibility(() => false)
    .linkVisibility(() => false);

  // Keep references for refresh
  container.__fg = fg;

  // Initial load
  refreshGraph({});

  // (Optional) expose for console tweaks
  window.refreshGraph = refreshGraph;

  function refreshGraph(overrides) {
    const params = { ...DEFAULT_QUERY, ...overrides };
    const queryStr = Object.entries(params)
      .filter(([_, v]) => v !== undefined && v !== null && v !== '')
      .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`)
      .join('&');

    const url = `${BACKEND_BASE}/data/graph?${queryStr}&_cb=${Date.now()}`;
    fetch(url)
      .then(r => r.json())
      .then(data => {
        buildScene(container, fg, data);
        console.log('Loaded meta:', data.meta);
      })
      .catch(e => console.error('Graph fetch error', e));
  }
});

/*
 * Core builder (idempotent for refresh)
 */
function buildScene(container, fg, data) {
  // Remove any prior custom objs (keep lights/camera)
  prunePrevious(fg.scene());

  // Preprocess coordinates
  const nodes = data.nodes || [];
  const links = data.links || [];

  if (!nodes.length) {
    console.warn('No nodes');
    return;
  }

  // Centering and optional scaling
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const n of nodes) {
    if (n.x < minX) minX = n.x;
    if (n.x > maxX) maxX = n.x;
    if (n.y < minY) minY = n.y;
    if (n.y > maxY) maxY = n.y;
  }
  const dx = maxX - minX || 1;
  const dy = maxY - minY || 1;
  const centerX = (minX + maxX) / 2;
  const centerY = (minY + maxY) / 2;
  const diagonal = Math.sqrt(dx * dx + dy * dy);
  let scale = 1;
  if (AUTO_SCALE && diagonal > TARGET_DIAGONAL && diagonal > 0) {
    scale = TARGET_DIAGONAL / diagonal;
  }

  // Attach fixed 3D coords
  for (const n of nodes) {
    n.fx = (n.x - centerX) * scale;
    n.fy = (n.y - centerY) * scale;
    n.fz = (Math.random() - 0.5) * Z_JITTER;
  }

  // Point cloud
  const N = nodes.length;
  const positions = new Float32Array(N * 3);
  const colors = new Float32Array(N * 3);
  const sizes = new Float32Array(N);

  let minFollowers = Infinity, maxFollowers = -Infinity;
  if (USE_FOLLOWER_SIZE) {
    for (const n of nodes) {
      const f = +n.followers || 0;
      if (f < minFollowers) minFollowers = f;
      if (f > maxFollowers) maxFollowers = f;
    }
    if (minFollowers === Infinity) {
      minFollowers = 0; maxFollowers = 1;
    }
  }

  const followerRange = (maxFollowers - minFollowers) || 1;

  nodes.forEach((n, i) => {
    const i3 = i * 3;
    positions[i3]     = n.fx;
    positions[i3 + 1] = n.fy;
    positions[i3 + 2] = n.fz;

    // Color mapping (simple gradient on followers or index)
    const t = USE_FOLLOWER_SIZE
      ? ((+n.followers || 0) - minFollowers) / followerRange
      : (i / N);
    colors[i3]     = t;
    colors[i3 + 1] = 0.4 * (1 - t);
    colors[i3 + 2] = 1 - t * 0.7;

    if (USE_FOLLOWER_SIZE) {
      const sz = POINT_SIZE_MIN + (POINT_SIZE_MAX - POINT_SIZE_MIN) * Math.pow(t, 0.5);
      sizes[i] = sz;
    } else {
      sizes[i] = POINT_SIZE;
    }
  });

  const geom = new THREE.BufferGeometry();
  geom.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  geom.setAttribute('color', new THREE.BufferAttribute(colors, 3));

  // If using per-point size with a custom shader, implement here; otherwise uniform size:
  const mat = new THREE.PointsMaterial({
    size: POINT_SIZE,
    vertexColors: true,
    sizeAttenuation: true
  });

  const points = new THREE.Points(geom, mat);
  points.frustumCulled = true;
  points.userData.__graphObject = true;
  fg.scene().add(points);

  // Build edges
  let usableLinks = links;
  if (CLIENT_EDGE_CAP != null && usableLinks.length > CLIENT_EDGE_CAP) {
    usableLinks = usableLinks.slice(0, CLIENT_EDGE_CAP);
  }

  if (usableLinks.length) {
    // Map id -> coords
    const idToNode = new Map(nodes.map(n => [n.id, n]));
    const L = usableLinks.length;
    const edgePos = new Float32Array(L * 6);
    let w = 0;
    for (let i = 0; i < L; i++) {
      const e = usableLinks[i];
      // backend may emit source/target as strings or objects
      const sId = typeof e.source === 'object' ? e.source.id : e.source;
      const tId = typeof e.target === 'object' ? e.target.id : e.target;
      const a = idToNode.get(sId);
      const b = idToNode.get(tId);
      if (!a || !b) continue;
      edgePos[w]     = a.fx; edgePos[w + 1] = a.fy; edgePos[w + 2] = a.fz;
      edgePos[w + 3] = b.fx; edgePos[w + 4] = b.fy; edgePos[w + 5] = b.fz;
      w += 6;
    }
    const trimmed = (w / 3);
    const finalPos = (w === edgePos.length) ? edgePos : edgePos.slice(0, w);

    const edgeGeom = new THREE.BufferGeometry();
    edgeGeom.setAttribute('position', new THREE.BufferAttribute(finalPos, 3));
    const edgeMat = new THREE.LineBasicMaterial({
      color: EDGE_COLOR,
      transparent: true,
      opacity: EDGE_OPACITY
    });
    const edgeLines = new THREE.LineSegments(edgeGeom, edgeMat);
    edgeLines.frustumCulled = true;
    edgeLines.userData.__graphObject = true;
    fg.scene().add(edgeLines);
  }

  // Camera positioning
  const camDist = computeOptimalZ(nodes);
  fg.cameraPosition({ z: camDist });

  // Simple hover
  initHover(container, fg, nodes, positions);
}

function prunePrevious(scene) {
  // Remove previous objects we added (flagged with userData.__graphObject)
  const toRemove = [];
  scene.traverse(obj => {
    if (obj.userData && obj.userData.__graphObject) toRemove.push(obj);
  });
  toRemove.forEach(o => {
    if (o.geometry) o.geometry.dispose();
    if (o.material) {
      if (Array.isArray(o.material)) o.material.forEach(m => m.dispose());
      else o.material.dispose();
    }
    scene.remove(o);
  });
}

function computeOptimalZ(nodes) {
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const n of nodes) {
    if (n.fx < minX) minX = n.fx;
    if (n.fx > maxX) maxX = n.fx;
    if (n.fy < minY) minY = n.fy;
    if (n.fy > maxY) maxY = n.fy;
  }
  const dx = maxX - minX;
  const dy = maxY - minY;
  const d = Math.sqrt(dx * dx + dy * dy);
  return d * 0.75;
}

function initHover(container, fg, nodes, positions) {
  const renderer = fg.renderer();
  const camera = fg.camera();
  const raycaster = new THREE.Raycaster();
  const mouse = new THREE.Vector2();
  let lastHover = null;
  const N = nodes.length;
  const threshold2 = HOVER_RADIUS * HOVER_RADIUS;

  function onMove(evt) {
    const rect = renderer.domElement.getBoundingClientRect();
    mouse.x = ((evt.clientX - rect.left) / rect.width) * 2 - 1;
    mouse.y = -((evt.clientY - rect.top) / rect.height) * 2 + 1;
    raycaster.setFromCamera(mouse, camera);

    const ro = raycaster.ray.origin;
    const rd = raycaster.ray.direction;

    let best = -1;
    let bestDist = threshold2;
    for (let i = 0; i < N; i++) {
      const x = positions[i * 3];
      const y = positions[i * 3 + 1];
      const z = positions[i * 3 + 2];

      // Vector from ray origin to point
      const vx = x - ro.x;
      const vy = y - ro.y;
      const vz = z - ro.z;
      const proj = vx * rd.x + vy * rd.y + vz * rd.z;
      if (proj < 0) continue;
      const rx = ro.x + rd.x * proj;
      const ry = ro.y + rd.y * proj;
      const rz = ro.z + rd.z * proj;
      const dx = x - rx;
      const dy = y - ry;
      const dz = z - rz;
      const dist2 = dx * dx + dy * dy + dz * dz;
      if (dist2 < bestDist) {
        bestDist = dist2;
        best = i;
      }
    }
    if (best !== -1 && best !== lastHover) {
      lastHover = best;
      const n = nodes[best];
      container.title = n.name || n.id || '';
    } else if (best === -1) {
      container.title = '';
    }
  }

  renderer.domElement.addEventListener('mousemove', onMove);
}
