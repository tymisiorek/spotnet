import ForceGraph3D from '3d-force-graph';
import * as THREE from 'three';

export const BACKEND_BASE = '';
export const EDGE_CAP = 5000;
export const SEG_CAP = 20000000;
export const SPHERE_RADIUS = 12;
export const EDGE_COLOR = 0x888888;
export const EDGE_OPACITY = 0.1;
export const PALETTE = [0x1f77b4,0xff7f0e,0x2ca02c,0xd62728,0x9467bd,0x8c564b,0xe377c2,0x7f7f7f,0xbcbd22,0x17becf,0xe6194b,0x3cb44b,0x4363d8,0xf58231,0x911eb4,0x46f0f0,0xf032e6,0xbcf60c,0xfabebe,0x008080];
export const FALLBACK_COLOR = 0x444444;
export const TOP_K = 25;
export const LOD_DISTANCES = { high: 0, medium: 10000, low: 50000 };
export const DISPLAY_FIELD = 'name';

export const state = {
  PICKABLE_MESHES: [],
  ACTIVE_HIT: null,
  TOOLTIP_EL: null,
  NAME_INDEX: null,
  HIGHLIGHT_MESH: null,
  UI: { WRAPPER:null, INPUT:null, BTN:null, STATUS:null, LOADING_OVERLAY:null, LOADING_STATUS:null, PROCEED_BTN:null },
  statusTimeoutId: null,
  userPlaylists: [],
  selectedPlaylistArtists: [],
  graphData: { nodes: [], links: [] },
  highlightObjects: [],
  nodeGroups: [],
  theGraph: null,
  lodUpdateCallback: null,
  lodUpdateHandle: null,
  lodUpdateUsesTimeout: false
};

const LOD_LEVELS = [
  { key: 'high', distance: LOD_DISTANCES.medium },
  { key: 'medium', distance: LOD_DISTANCES.low },
  { key: 'low', distance: Infinity }
];

const LOD_KEYS = LOD_LEVELS.map(l => l.key);

const SHARED_SPHERE_GEOMETRIES = {
  high: new THREE.SphereGeometry(SPHERE_RADIUS, 16, 16),
  medium: new THREE.SphereGeometry(SPHERE_RADIUS, 10, 10),
  low: new THREE.SphereGeometry(SPHERE_RADIUS, 6, 6)
};

export function setStatus(msg, ok = true) {
  const el = state.UI.STATUS;
  if (!el) {
    return;
  }
  if (state.statusTimeoutId) {
    clearTimeout(state.statusTimeoutId);
  }
  el.textContent = msg;
  el.className = 'status-notification';
  el.classList.add(ok ? 'success' : 'error', 'show');
  state.statusTimeoutId = setTimeout(() => {
    el.classList.remove('show');
  }, 3500);
}

export function clearFocus() {
  state.ACTIVE_HIT = null;
  hideTooltip();
  highlightOff();
}

export function initGraph(el) {
  state.theGraph = ForceGraph3D({
    rendererConfig: { antialias:true, powerPreference:'high-performance', logarithmicDepthBuffer:false, precision:'highp' }
  })(el)
    .graphData({ nodes:[], links:[] })
    .d3Force('charge', null)
    .cooldownTicks(0)
    .enableNodeDrag(false)
    .linkVisibility(() => false);

  state.theGraph.scene().add(new THREE.AmbientLight(0xffffff, 0.7));
  const dirLight = new THREE.DirectionalLight(0xffffff, 0.5);
  dirLight.position.set(1,1,1).normalize();
  state.theGraph.scene().add(dirLight);

  const cam = state.theGraph.camera();
  cam.near = 0.1;
  cam.far = 1e9;
  cam.updateProjectionMatrix();

  const controls = state.theGraph.controls();
  Object.assign(controls, {
    screenSpacePanning:false, minDistance:100, maxDistance:1_000_000,
    maxPolarAngle: Math.PI/2, enableDamping:true, dampingFactor:0.05
  });
  if (controls.constraint) {
    controls.constraint.smoothZoom = true;
    controls.constraint.zoomDampingFactor = 0.2;
    controls.constraint.smoothZoomSpeed = 5.0;
  }
  return state.theGraph;
}

export function ensureTooltip(containerEl) {
  if (state.TOOLTIP_EL) {
    return;
  }
  const el = document.createElement('div');
  Object.assign(el.style, {
    position:'absolute', pointerEvents:'none', transform:'translate(-50%, -120%)',
    padding:'6px 8px', font:'12px/1.2 system-ui,sans-serif', background:'rgba(0,0,0,0.75)',
    color:'#fff', borderRadius:'6px', whiteSpace:'nowrap', display:'none', zIndex:10
  });
  state.TOOLTIP_EL = el;
  containerEl.appendChild(el);
}

export function ensureHighlightMesh() {
  if (state.HIGHLIGHT_MESH) {
    return;
  }
  const geom = new THREE.SphereGeometry(SPHERE_RADIUS*1.7, 32, 32);
  const mat = new THREE.MeshBasicMaterial({ color:0xffffff, wireframe:true, depthTest:false, transparent:true, opacity:0.9 });
  state.HIGHLIGHT_MESH = new THREE.Mesh(geom, mat);
  state.HIGHLIGHT_MESH.visible = false;
  state.HIGHLIGHT_MESH.renderOrder = 9999;
  state.theGraph.scene().add(state.HIGHLIGHT_MESH);
}

export function highlightAt(x,y,z){
  if (state.HIGHLIGHT_MESH) {
    state.HIGHLIGHT_MESH.position.set(x,y,z);
    state.HIGHLIGHT_MESH.visible = true;
  }
}

export function highlightOff(){
  if (state.HIGHLIGHT_MESH) {
    state.HIGHLIGHT_MESH.visible = false;
  }
}

export function showTooltip(node, point){
  state.ACTIVE_HIT = { node, point: point.clone() };
  const text = node?.[DISPLAY_FIELD] ?? '(unknown)';
  state.TOOLTIP_EL.textContent = String(text);
  state.TOOLTIP_EL.style.display = 'block';
  updateTooltipPosition();
}

export function hideTooltip(){
  state.ACTIVE_HIT = null;
  if (state.TOOLTIP_EL) {
    state.TOOLTIP_EL.style.display = 'none';
  }
}

export function updateTooltipPosition(){
  if (!state.ACTIVE_HIT || !state.TOOLTIP_EL) {
    return;
  }
  const canvas = state.theGraph.renderer().domElement;
  const cam = state.theGraph.camera();
  const p = state.ACTIVE_HIT.point.clone().project(cam);
  const halfW = canvas.clientWidth/2;
  const halfH = canvas.clientHeight/2;
  state.TOOLTIP_EL.style.left = `${(p.x*halfW)+halfW}px`;
  state.TOOLTIP_EL.style.top  = `${(-p.y*halfH)+halfH}px`;
}

export function normalizeName(s) {
  if (typeof s === 'string') {
    return s.trim().toLowerCase();
  } else {
    return '';
  }
}

export function screenToNDC(evt, canvas, outVec2) {
  const rect = canvas.getBoundingClientRect();
  const x = ((evt.clientX - rect.left) / rect.width) * 2 - 1;
  const y = -((evt.clientY - rect.top) / rect.height) * 2 + 1;
  outVec2.set(x, y);
}

export function parsePoints(p){
  if (!p) {
    return null;
  }
  if (Array.isArray(p)) {
    return p.map(o => ({ x:+o.x, y:+o.y, z:+o.z }));
  }
  if (typeof p === 'string') {
    return p.split('|').map(s => {
      const [xs, ys, zs] = s.split(',');
      return { x:+xs, y:+ys, z:+zs };
    });
  }
  return null;
}

export function computeBBox(nodes){
  let minX = Infinity;
  let maxX = -Infinity;
  let minY = Infinity;
  let maxY = -Infinity;
  let minZ = Infinity;
  let maxZ = -Infinity;

  for (const n of nodes){
    if (n.x < minX) {
      minX = n.x;
    }
    if (n.x > maxX) {
      maxX = n.x;
    }
    if (n.y < minY) {
      minY = n.y;
    }
    if (n.y > maxY) {
      maxY = n.y;
    }
    if (n.z < minZ) {
      minZ = n.z;
    }
    if (n.z > maxZ) {
      maxZ = n.z;
    }
  }

  const center = { x:(minX+maxX)/2, y:(minY+maxY)/2, z:(minZ+maxZ)/2 };
  const diag = Math.hypot(maxX - minX, maxY - minY, maxZ - minZ);
  return { center, diag };
}

// Node instancing infrastructure with manual LOD management per color group
function clearInstancedNodes() {
  if (!state.nodeGroups?.length) {
    return;
  }
  state.nodeGroups.forEach(group => {
    LOD_KEYS.forEach(level => {
      const mesh = group.meshes[level];
      if (mesh) {
        state.theGraph.scene().remove(mesh);
        mesh.geometry?.dispose?.();
        mesh.geometry = null;
        mesh.userData.nodes = null;
        mesh.material = null;
      }
    });
    group.material?.dispose?.();
    group.material = null;
  });
  state.nodeGroups = [];
  state.PICKABLE_MESHES.length = 0;
  if (state.lodUpdateHandle !== null) {
    if (state.lodUpdateUsesTimeout) {
      clearTimeout(state.lodUpdateHandle);
    } else if (typeof cancelAnimationFrame === 'function') {
      cancelAnimationFrame(state.lodUpdateHandle);
    }
    state.lodUpdateHandle = null;
    state.lodUpdateUsesTimeout = false;
  }
  state.lodUpdateCallback = null;
}

function calculateGroupCenter(nodes) {
  const center = new THREE.Vector3();
  if (!nodes.length) {
    return center;
  }
  for (const node of nodes) {
    center.x += node.x;
    center.y += node.y;
    center.z += node.z || 0;
  }
  center.multiplyScalar(1 / nodes.length);
  return center;
}

function selectLODLevel(distance) {
  for (const level of LOD_LEVELS) {
    if (distance <= level.distance) {
      return level.key;
    }
  }
  return 'low';
}

function updateInstancedNodeLODs(camera) {
  if (!camera || !state.nodeGroups?.length) {
    return;
  }
  const camPos = camera.position;
  state.nodeGroups.forEach(group => {
    if (group.hidden) {
      LOD_KEYS.forEach(level => {
        const mesh = group.meshes[level];
        if (mesh) mesh.visible = false;
      });
      return;
    }
    const distance = camPos.distanceTo(group.center);
    const nextLevel = selectLODLevel(distance);
    if (nextLevel === group.currentLevel) {
      return;
    }
    group.currentLevel = nextLevel;
    LOD_KEYS.forEach(level => {
      const mesh = group.meshes[level];
      if (!mesh) {
        return;
      }
      mesh.visible = level === nextLevel;
    });
  });
}

export function setupInstancedNodes(nodes, topCommunities){
  clearInstancedNodes();

  const byColor = {};
  nodes.forEach(n=>{
    const idx = topCommunities.indexOf(+n.community);
    const color = idx>=0 ? PALETTE[idx % PALETTE.length] : FALLBACK_COLOR;
    (byColor[color] ||= []).push(n);
  });

  const nodeGroups = [];
  Object.entries(byColor).forEach(([color, arr])=>{
    const material = new THREE.MeshStandardMaterial({ color:+color, metalness:0.3, roughness:0.6 });
    const matrix = new THREE.Matrix4();
    const meshes = {};

    LOD_KEYS.forEach(level => {
      const geometry = SHARED_SPHERE_GEOMETRIES[level].clone();
      const mesh = new THREE.InstancedMesh(geometry, material, arr.length);
      mesh.instanceMatrix.setUsage(THREE.StaticDrawUsage);
      mesh.visible = level === 'high';
      arr.forEach((node,i)=>{
        matrix.setPosition(node.x, node.y, node.z || 0);
        mesh.setMatrixAt(i, matrix);
      });
      mesh.instanceMatrix.needsUpdate = true;
      mesh.userData.nodes = arr;
      mesh.frustumCulled = false;
      meshes[level] = mesh;
      state.theGraph.scene().add(mesh);
      state.PICKABLE_MESHES.push(mesh);
    });

    const center = calculateGroupCenter(arr);
    nodeGroups.push({ meshes, center, currentLevel: 'high', material, hidden: false });
  });

  state.nodeGroups = nodeGroups;

  if (state.theGraph) {
    updateInstancedNodeLODs(state.theGraph.camera());
  }

  if (!state.lodUpdateCallback) {
    state.lodUpdateCallback = () => {
      if (state.theGraph) {
        updateInstancedNodeLODs(state.theGraph.camera());
      }
      const raf = typeof requestAnimationFrame === 'function';
      state.lodUpdateUsesTimeout = !raf;
      state.lodUpdateHandle = raf
        ? requestAnimationFrame(state.lodUpdateCallback)
        : setTimeout(state.lodUpdateCallback, 16);
    };
  }

  if (state.lodUpdateHandle === null) {
    state.lodUpdateCallback();
  }

  state.theGraph.nodeThreeObject(()=> new THREE.Object3D());
}

function setInstancedNodeVisibility(visible) {
  if (!state.nodeGroups?.length) {
    return;
  }
  state.nodeGroups.forEach(group => {
    group.hidden = !visible;
    if (visible) {
      group.currentLevel = null;
    }
    LOD_KEYS.forEach(level => {
      const mesh = group.meshes[level];
      if (!mesh) {
        return;
      }
      mesh.visible = visible ? level === group.currentLevel : false;
    });
  });
  if (visible && state.theGraph) {
    updateInstancedNodeLODs(state.theGraph.camera());
  }
}

export function hideInstancedNodes() {
  setInstancedNodeVisibility(false);
}

export function showInstancedNodes() {
  setInstancedNodeVisibility(true);
}

export function buildNameIndex(nodes){
  const idx = new Map();
  for (const n of nodes){
    const k = normalizeName(n?.name);
    if (k) {
      idx.set(k, n);
    }
  }
  state.NAME_INDEX = idx;
}

export function focusNodeByName(rawName){
  const idx = state.NAME_INDEX;
  if (!idx) {
    return;
  }

  const node = idx.get(normalizeName(rawName));
  if (!node) {
    setStatus('Artist not found', false);
    hideTooltip();
    highlightOff();
    return;
  }

  const THREEv = new THREE.Vector3(node.x,node.y,node.z||0);
  const ctrls = state.theGraph.controls();
  const cam = state.theGraph.camera();

  ctrls.target.copy(THREEv);

  const dist = SPHERE_RADIUS*80;
  cam.position.copy(THREEv.clone().add(new THREE.Vector3(0,0,1).multiplyScalar(dist)));
  cam.updateProjectionMatrix();

  showTooltip(node, THREEv);
  highlightAt(THREEv.x,THREEv.y,THREEv.z);
  setStatus(`Focused on: ${node.name||''}`, true);
}

export function addOptimizedBundledEdges(links){
  let segCount = 0;

  for (const e of links){
    const pts = parsePoints(e.points);
    if (pts && pts.length > 1) {
      segCount += pts.length - 1;
    }
    if (SEG_CAP != null && segCount >= SEG_CAP) {
      break;
    }
  }

  const positions = new Float32Array(segCount*2*3);
  let w = 0;

  for (const e of links){
    const pts = parsePoints(e.points);
    if (!pts || pts.length < 2) {
      continue;
    }
    for (let i = 0; i < pts.length - 1; i++){
      if (SEG_CAP != null && (w/3) >= SEG_CAP * 2) {
        break;
      }
      const a = pts[i];
      const b = pts[i+1];
      positions[w]   = a.x; positions[w+1] = a.y; positions[w+2] = a.z;
      positions[w+3] = b.x; positions[w+4] = b.y; positions[w+5] = b.z;
      w += 6;
    }
    if (SEG_CAP != null && (w/3) >= SEG_CAP * 2) {
      break;
    }
  }

  const geom = new THREE.BufferGeometry();
  const finalArray = (w === positions.length) ? positions : positions.slice(0, w);
  geom.setAttribute('position', new THREE.BufferAttribute(finalArray, 3));
  geom.computeBoundingSphere();

  const mat = new THREE.LineBasicMaterial({ color:EDGE_COLOR, transparent:true, opacity:EDGE_OPACITY });
  const lines = new THREE.LineSegments(geom, mat);
  lines.frustumCulled = true;
  lines.userData.__graphObject = true;
  state.theGraph.scene().add(lines);
}

export function installPicking() {
  const canvas = state.theGraph.renderer().domElement;
  const raycaster = new THREE.Raycaster();
  const mouse = new THREE.Vector2();
  raycaster.params.Mesh = { ...(raycaster.params.Mesh || {}), threshold: SPHERE_RADIUS * 0.4 };

  canvas.addEventListener('contextmenu', (evt) => {
    evt.preventDefault();
    clearFocus();
  });

  canvas.addEventListener('pointerdown', (evt) => {
    // Right click unfocus/unhighlight
    if (evt.button === 2) {
      evt.preventDefault();
      clearFocus();
      return;
    }

    if (evt.button !== 0) {
      return;
    }

    screenToNDC(evt, canvas, mouse);
    raycaster.setFromCamera(mouse, state.theGraph.camera());
    const hits = raycaster.intersectObjects(state.PICKABLE_MESHES, false);

    if (!hits.length) {
      return;
    }

    const hit = hits[0];
    const arr = hit.object.userData?.nodes;
    const node = (arr && hit.instanceId != null) ? arr[hit.instanceId] : null;

    if (!node) {
      return;
    }

    state.ACTIVE_HIT = { node, point: hit.point.clone() };
    showTooltip(node, hit.point);
    highlightAt(node.x, node.y, node.z || 0);
  });
}
