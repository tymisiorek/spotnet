// graph.js
import ForceGraph3D from '3d-force-graph';
import * as THREE from 'three';

//
// ======= knobs =======
const BACKEND_BASE = 'http://127.0.0.1:8000';
const EDGE_CAP     = 50000;     // null => all
const NODE_SIZE    = 4;
const NODE_COLOR   = 0x4aa3ff;
const EDGE_COLOR   = 0x888888;
const EDGE_OPACITY = 0.2;
// =====================

document.addEventListener('DOMContentLoaded', () => {
  const el = document.getElementById('3d-graph');
  if (!el) return;

  // Init FG3D but turn almost everything off
  const fg = ForceGraph3D()(el)
    .graphData({ nodes: [], links: [] })
    .d3Force('charge', null)
    .cooldownTicks(0)
    .enableNodeDrag(false)
    .nodeVisibility(() => false)   // we'll render our own points
    .linkVisibility(() => false);  // render our own edges

  const url = `${BACKEND_BASE}/data/graph?_cb=${Date.now()}`;
  fetch(url).then(r => r.json()).then(data => {
    let { nodes, links } = data;
    if (EDGE_CAP != null && links.length > EDGE_CAP) links = links.slice(0, EDGE_CAP);

    // lock coords
    for (const n of nodes) {
      n.fx = n.x;
      n.fy = n.y;
      n.fz = n.z ?? 0;
    }

    // Keep FG aware of nodes/links for possible interaction later
    fg.graphData({ nodes, links });

    // Build batched meshes
    addPointCloud(fg.scene(), nodes);
    addEdgeSegments(fg.scene(), nodes, links);

    // Camera
    fg.cameraPosition({ z: autoZ(nodes) });
  }).catch(console.error);
});

function addPointCloud(scene, nodes) {
  const N = nodes.length;
  const positions = new Float32Array(N * 3);
  const colors    = new Float32Array(N * 3);
  const c = new THREE.Color(NODE_COLOR);

  for (let i = 0; i < N; i++) {
    const n = nodes[i];
    const i3 = i * 3;
    positions[i3]     = n.fx;
    positions[i3 + 1] = n.fy;
    positions[i3 + 2] = n.fz;

    colors[i3]     = c.r;
    colors[i3 + 1] = c.g;
    colors[i3 + 2] = c.b;
  }

  const geom = new THREE.BufferGeometry();
  geom.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  geom.setAttribute('color',    new THREE.BufferAttribute(colors, 3));
  geom.computeBoundingSphere();

  const mat = new THREE.PointsMaterial({
    size: NODE_SIZE,
    vertexColors: true,
    sizeAttenuation: true
  });

  const points = new THREE.Points(geom, mat);
  points.frustumCulled = true;
  points.userData.__graphObject = true;
  scene.add(points);
}

function addEdgeSegments(scene, nodes, links) {
  const id2node = new Map(nodes.map(n => [n.id, n]));
  const L = links.length;

  // Pre-allocate Float32Array (2 endpoints * 3 comps)
  const pos = new Float32Array(L * 6);
  let w = 0;

  for (let i = 0; i < L; i++) {
    const e = links[i];
    const s = id2node.get(e.source);
    const t = id2node.get(e.target);
    if (!s || !t) continue;

    pos[w]     = s.fx; pos[w + 1] = s.fy; pos[w + 2] = s.fz;
    pos[w + 3] = t.fx; pos[w + 4] = t.fy; pos[w + 5] = t.fz;
    w += 6;
  }

  const finalPos = (w === pos.length) ? pos : pos.slice(0, w);

  const geom = new THREE.BufferGeometry();
  geom.setAttribute('position', new THREE.BufferAttribute(finalPos, 3));
  geom.computeBoundingSphere();

  const mat = new THREE.LineBasicMaterial({
    color: EDGE_COLOR,
    transparent: true,
    opacity: EDGE_OPACITY
  });

  const lines = new THREE.LineSegments(geom, mat);
  lines.frustumCulled = true;
  lines.userData.__graphObject = true;
  scene.add(lines);
}

function autoZ(nodes) {
  if (!nodes.length) return 1000;
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  for (const n of nodes) {
    if (n.x < minX) minX = n.x;
    if (n.x > maxX) maxX = n.x;
    if (n.y < minY) minY = n.y;
    if (n.y > maxY) maxY = n.y;
  }
  const d = Math.hypot(maxX - minX, maxY - minY);
  return d * 0.8;
}
