// graph.js  – draw coloured 3‑D spheres without instancing
import ForceGraph3D from '3d-force-graph';
import * as THREE from 'three';

const BACKEND_BASE = 'http://127.0.0.1:8000';

// ---------- tweakables -----------------------------------------------------
const EDGE_CAP   = 10000;
const SEG_CAP    = 5000000;

const SPHERE_RADIUS = 4;      // sphere radius (world units)
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
const TOP_K = 20;                    // number of communities to colour
// --------------------------------------------------------------------------

document.addEventListener('DOMContentLoaded', () => {
  const el = document.getElementById('3d-graph');
  if (!el) return;

  /* -----------  create ForceGraph3D instance  --------------------------- */
  const fg = ForceGraph3D({
    rendererConfig: {
      antialias: true,
      powerPreference: 'high-performance',
      logarithmicDepthBuffer: true
    }
  })(el)
    .graphData({ nodes: [], links: [] })
    .d3Force('charge', null)
    .cooldownTicks(0)
    .enableNodeDrag(false)
    .linkVisibility(() => false);      // we draw edges manually

  /* ---------------- lighting ------------------------------------------- */
  fg.scene().add(new THREE.AmbientLight(0xffffff, 0.7));
  const dirLight = new THREE.DirectionalLight(0xffffff, 0.5);
  dirLight.position.set(1, 1, 1).normalize();
  fg.scene().add(dirLight);

  /* -------------- camera / controls tweaks ----------------------------- */
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

  /* ---------------- fetch graph data ----------------------------------- */
  const url = `${BACKEND_BASE}/data/graph?_cb=${Date.now()}`;
  fetch(url)
    .then(r => r.json())
    .then(data => {
      let { nodes, links } = data;
      if (EDGE_CAP != null && links.length > EDGE_CAP) {
        links = links.slice(0, EDGE_CAP);
      }

      /* lock node positions – ForceGraph3D will respect fx/y/z */
      nodes.forEach(n => {
        n.fx = n.x;
        n.fy = n.y;
        n.fz = n.z ?? 0;
      });

      /* ---------- determine the TOP_K largest communities -------------- */
      const counts = {};
      nodes.forEach(n => {
        const id = +n.community;
        counts[id] = (counts[id] || 0) + 1;
      });
      const topCommunities = Object.entries(counts)
        .sort((a, b) => b[1] - a[1])
        .slice(0, TOP_K)
        .map(([id]) => +id);

      /* ---------- per‑node sphere (Mesh) ------------------------------- */
      const sphereGeo = new THREE.SphereGeometry(SPHERE_RADIUS, 8, 8);
      fg.nodeThreeObject(node => {
        const comm = +node.community;
        const idx  = topCommunities.indexOf(comm);
        const color = idx >= 0 ? PALETTE[idx % PALETTE.length] : FALLBACK_COLOR;
        const mat   = new THREE.MeshBasicMaterial({ color });
        return new THREE.Mesh(sphereGeo, mat);
      });

      fg.graphData({ nodes, links });
      addBundledEdges(fg.scene(), links);

      /* ---------- camera centring -------------------------------------- */
      const { center, diag } = computeBBox(nodes);
      controls.target.set(center.x, center.y, center.z);
      cam.position.set(center.x, center.y, center.z + diag * 0.8);
      cam.updateProjectionMatrix();
    })
    .catch(console.error);
});

/* --------------------- edge bundling ------------------------------------ */
function addBundledEdges(scene, links) {
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
    opacity: EDGE_OPACITY
  });

  const lines = new THREE.LineSegments(geom, mat);
  lines.frustumCulled       = true;
  lines.userData.__graphObject = true;
  scene.add(lines);
}

/* --------------------- helpers ------------------------------------------ */
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
