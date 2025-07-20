// graph.js
import * as THREE from 'three';
import ForceGraph3D from '3d-force-graph';

const BACKEND_BASE = 'http://127.0.0.1:8000'; 
const POINT_SIZE = 4.5;
const COMMUNITY_COLORS  = [
  0x1f77b4, 0xaec7e8, 0xff7f0e, 0xffbb78, 0x2ca02c,
  0x98df8a, 0xd62728, 0xff9896, 0x9467bd, 0xc5b0d5,
  0x8c564b, 0xc49c94, 0xe377c2, 0xf7b6d2, 0x7f7f7f,
  0xc7c7c7, 0xbcbd22, 0xdbdb8d, 0x17becf, 0x9edae5
];

const GALAXY_ARMS = 4;     // number of spiral arms
const TWIST = 2.0;   // spiral tightness
const SKELETON_MIN = 200;   // min points per arm skeleton
const SKELETON_MAX = 500;   // max points per arm skeleton
const PERP_OFFSET_FRACT = 0.02;  // 2% of R_MAX for twig fan‑out
const TWIG_OPACITY = 0.08;  // twig edge opacity


document.addEventListener('DOMContentLoaded', () => {
  const container = document.getElementById('3d-graph');
  if (!container) return;

  const fg = ForceGraph3D()(container)
    .d3Force('charge', null)  // disable dynamic forces
    .cooldownTicks(0)
    .enableNodeDrag(false)
    .nodeVisibility(() => false)  // render custom points
    .linkVisibility(() => false); // render custom lines

  // Fetch graph.json
  fetch(`${BACKEND_BASE}/data/graph?_cb=${Date.now()}`)
    .then(res => res.json())
    .then(data => buildScene(container, fg, data))
    .catch(err => console.error('Graph fetch error', err));
});


function buildScene(container, fg, data) {
  prunePrevious(fg.scene());

  const nodes = data.nodes || [];
  const links = data.links || [];
  if (!nodes.length) {
    console.warn('No nodes to render');
    return;
  }

  // 1) Fix positions & properties
  nodes.forEach(n => {
    n.fx = n.x;
    n.fy = n.y;
    n.fz = n.z;
    n.arm = (typeof n.arm === 'number') ? n.arm : 0;
    n.community = (typeof n.community === 'number') ? n.community : 0;
  });

  // 2) Center + scale XY (preserve Z)
  const { centerX, centerY, scale, R_MAX } = centerAndScale(nodes);
  nodes.forEach(n => {
    n.fx = (n.fx - centerX) * scale;
    n.fy = (n.fy - centerY) * scale;
  });

  // 3) Build per-arm skeleton curves
  const skeletons = buildSkeletons(GALAXY_ARMS, R_MAX);

  // 4) Build bundled twig edges
  const twigMesh = buildSkeletonBundledEdges(nodes, links, skeletons, R_MAX);
  fg.scene().add(twigMesh);

  // 5) Build node point cloud
  const nodeCloud = buildNodePointCloud(nodes);
  fg.scene().add(nodeCloud);

  // 6) Camera positioning and hover tooltips
  fg.cameraPosition({ z: computeOptimalZ(nodes) });
  initHover(container, fg, nodes, nodeCloud.geometry.getAttribute('position').array);
}

/* ================= Helper Functions ================= */

// Compute center, scale factor, and post-scale R_MAX
function centerAndScale(nodes) {
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  nodes.forEach(n => {
    if (n.fx < minX) minX = n.fx;
    if (n.fx > maxX) maxX = n.fx;
    if (n.fy < minY) minY = n.fy;
    if (n.fy > maxY) maxY = n.fy;
  });
  const dx = maxX - minX || 1, dy = maxY - minY || 1;
  const diag = Math.sqrt(dx*dx + dy*dy);
  const scale = diag > 4000 ? 4000/diag : 1; // optional auto-scale
  return {
    centerX: (minX + maxX)/2,
    centerY: (minY + maxY)/2,
    scale,
    R_MAX: (diag/2) * scale
  };
}

// Build random skeleton points for each arm
function buildSkeletons(arms, R_MAX) {
  const count = Math.floor(Math.random() * (SKELETON_MAX - SKELETON_MIN + 1)) + SKELETON_MIN;
  const skeletons = [];
  for (let a = 0; a < arms; a++) {
    const pts = [];
    for (let i = 0; i < count; i++) {
      const t = i/(count - 1);
      const r = t * R_MAX;
      const theta = (2*Math.PI*a)/arms + TWIST * t;
      pts.push(new THREE.Vector3(r*Math.cos(theta), r*Math.sin(theta), 0));
    }
    skeletons.push(pts);
  }
  return skeletons;
}

// Route edges as quadratic Béziers along arm skeletons
function buildSkeletonBundledEdges(nodes, links, skeletons, R_MAX) {
  const idMap = new Map(nodes.map(n => [n.id, n]));
  const posArr = [], colArr = [];

  links.forEach(e => {
    const s = idMap.get(typeof e.source === 'object' ? e.source.id : e.source);
    const t = idMap.get(typeof e.target === 'object' ? e.target.id : e.target);
    if (!s || !t) return;

    const sx=s.fx, sy=s.fy, sz=s.fz;
    const tx=t.fx, ty=t.fy, tz=t.fz;
    const mx=(sx+tx)/2, my=(sy+ty)/2;
    const dist = Math.hypot(tx-sx, ty-sy);
    if (!dist) return;

    // Select skeleton by source’s arm
    const armIndex = s.arm % skeletons.length;
    const sk = skeletons[armIndex];

    // Project midpoint onto skeleton by index proportional to radius
    const rM = Math.hypot(mx, my);
    const ti = Math.floor((rM / R_MAX) * (sk.length - 1));
    const idx = Math.max(0, Math.min(sk.length - 1, ti));
    const C0 = sk[idx];

    // Estimate tangent & get perpendicular for fan offset
    const prev = sk[Math.max(0, idx-1)], nxt = sk[Math.min(sk.length-1, idx+1)];
    let dx = nxt.x - prev.x, dy = nxt.y - prev.y;
    const dlen = Math.hypot(dx, dy) || 1;
    dx /= dlen; dy /= dlen;
    const px = -dy, py = dx;
    const offset = PERP_OFFSET_FRACT * R_MAX;
    const Cx = C0.x + px * offset;
    const Cy = C0.y + py * offset;
    const Cz = (sz + tz) / 2;

    // Subdivide quadratic Bézier from (s->C->t)
    const segCount = Math.max(4, Math.min(20, Math.round(dist / 30)));
    let prevX = sx, prevY = sy, prevZ = sz;
    for (let i = 1; i <= segCount; i++) {
      const tnorm = i/segCount, omt = 1 - tnorm;
      const x = omt*omt*sx + 2*omt*tnorm*Cx + tnorm*tnorm*tx;
      const y = omt*omt*sy + 2*omt*tnorm*Cy + tnorm*tnorm*ty;
      const z = omt*omt*sz + 2*omt*tnorm*Cz + tnorm*tnorm*tz;

      posArr.push(prevX, prevY, prevZ, x, y, z);

      // Color by source community
      const hex = COMMUNITY_COLORS[s.community % COMMUNITY_COLORS.length];
      const rc = ((hex >> 16) & 255) / 255;
      const gc = ((hex >> 8)  & 255) / 255;
      const bc = ( hex        & 255) / 255;
      colArr.push(rc, gc, bc, rc, gc, bc);

      prevX = x; prevY = y; prevZ = z;
    }
  });

  const geom = new THREE.BufferGeometry();
  geom.setAttribute('position', new THREE.BufferAttribute(new Float32Array(posArr), 3));
  geom.setAttribute('color',    new THREE.BufferAttribute(new Float32Array(colArr), 3));

  const mat = new THREE.LineBasicMaterial({
    vertexColors: true,
    transparent:   true,
    opacity:       TWIG_OPACITY
  });

  const lines = new THREE.LineSegments(geom, mat);
  lines.userData.__graphObject = true;
  return lines;
}

// Create circular sprite nodes colored by community
function buildNodePointCloud(nodes) {
  const N   = nodes.length;
  const pos = new Float32Array(N * 3);
  const col = new Float32Array(N * 3);

  nodes.forEach((n, i) => {
    const i3 = 3 * i;
    pos[i3]   = n.fx;
    pos[i3+1] = n.fy;
    pos[i3+2] = n.fz;

    const hex = COMMUNITY_COLORS[n.community % COMMUNITY_COLORS.length];
    col[i3]   = ((hex >> 16) & 255) / 255;
    col[i3+1] = ((hex >> 8)  & 255) / 255;
    col[i3+2] = ( hex        & 255) / 255;
  });

  const geom = new THREE.BufferGeometry();
  geom.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  geom.setAttribute('color',    new THREE.BufferAttribute(col, 3));

  // Create circular texture
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = 64;
  const ctx = canvas.getContext('2d');
  ctx.beginPath();
  ctx.arc(32, 32, 31, 0, 2 * Math.PI);
  ctx.fillStyle = '#ffffff';
  ctx.fill();
  const tex = new THREE.Texture(canvas);
  tex.needsUpdate = true;

  const mat = new THREE.PointsMaterial({
    size:            POINT_SIZE,
    map:             tex,
    vertexColors:    true,
    sizeAttenuation: true,
    transparent:     true,
    alphaTest:       0.1,
    depthWrite:      false
  });

  const points = new THREE.Points(geom, mat);
  points.userData.__graphObject = true;
  return points;
}

// Remove previous graph objects
function prunePrevious(scene) {
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

// Compute camera Z distance based on XY spread
function computeOptimalZ(nodes) {
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  nodes.forEach(n => {
    if (n.fx < minX) minX = n.fx;
    if (n.fx > maxX) maxX = n.fx;
    if (n.fy < minY) minY = n.fy;
    if (n.fy > maxY) maxY = n.fy;
  });
  const dx = maxX - minX, dy = maxY - minY;
  return Math.sqrt(dx * dx + dy * dy) * 0.75;
}

// Hover tooltip on mouse move
function initHover(container, fg, nodes, positions) {
  const renderer = fg.renderer();
  const camera   = fg.camera();
  const ray      = new THREE.Raycaster();
  const mouse    = new THREE.Vector2();
  let lastHover  = -1;
  const N        = nodes.length;
  const thr2     = 30 * 30;

  renderer.domElement.addEventListener('mousemove', evt => {
    const rect = renderer.domElement.getBoundingClientRect();
    mouse.x = ((evt.clientX - rect.left) / rect.width) * 2 - 1;
    mouse.y = -((evt.clientY - rect.top) / rect.height) * 2 + 1;
    ray.setFromCamera(mouse, camera);

    const ro = ray.ray.origin;
    const rd = ray.ray.direction;
    let best = -1, bestD = thr2;

    for (let i = 0; i < N; i++) {
      const x = positions[3*i], y = positions[3*i+1], z = positions[3*i+2];
      const vx = x - ro.x, vy = y - ro.y, vz = z - ro.z;
      const proj = vx*rd.x + vy*rd.y + vz*rd.z;
      if (proj < 0) continue;
      const rx = ro.x + rd.x*proj;
      const ry = ro.y + rd.y*proj;
      const rz = ro.z + rd.z*proj;
      const dx = x - rx, dy = y - ry, dz = z - rz;
      const d2 = dx*dx + dy*dy + dz*dz;
      if (d2 < bestD) { bestD = d2; best = i; }
    }

    if (best !== -1 && best !== lastHover) {
      lastHover = best;
      container.title = nodes[best].name || nodes[best].id || '';
    } else if (best === -1) {
      container.title = '';
    }
  });
}
