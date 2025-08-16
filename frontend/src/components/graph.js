import ForceGraph3D from '3d-force-graph';
import * as THREE from 'three';

const BACKEND_BASE = '';

//proxy to flask
document.addEventListener('DOMContentLoaded', () => {
  const loginBtn = document.getElementById('loginBtn');
  if (loginBtn) {
    loginBtn.addEventListener('click', () => {
      window.location.href = '/auth/login';
    });
  }
});

const EDGE_CAP = 1500000;
const SEG_CAP = 200000000;
const SPHERE_RADIUS = 10;
const EDGE_COLOR = 0x888888;
const EDGE_OPACITY = 0.15;

// Right now only have the first 20 communities, but everything should be colored later on
const PALETTE = [
    0x1f77b4, 0xff7f0e, 0x2ca02c, 0xd62728, 0x9467bd,
    0x8c564b, 0xe377c2, 0x7f7f7f, 0xbcbd22, 0x17becf,
    0xe6194b, 0x3cb44b, 0x4363d8, 0xf58231, 0x911eb4,
    0x46f0f0, 0xf032e6, 0xbcf60c, 0xfabebe, 0x008080
];
const FALLBACK_COLOR = 0x444444;
const TOP_K = 25;

const LOD_DISTANCES = { high: 0, medium: 10000, low: 50000 };

const DISPLAY_FIELD = 'name';
let PICKABLE_MESHES = [];
let ACTIVE_HIT = null;
let TOOLTIP_EL = null;

let NAME_INDEX = null;
let HIGHLIGHT_MESH = null;

let UI_WRAPPER = null;
let UI_INPUT = null;
let UI_BTN = null;
let UI_STATUS = null;
let UI_LOADING_OVERLAY = null;
let UI_LOADING_STATUS = null;
let UI_PROCEED_BTN = null;


document.addEventListener('DOMContentLoaded', () => {
    const el = document.getElementById('3d-graph');
    if (!el) return;

    UI_WRAPPER = document.getElementById('ui');
    UI_INPUT = document.getElementById('searchInput');
    UI_BTN = document.getElementById('searchBtn');
    UI_STATUS = document.getElementById('searchStatus');
    UI_LOADING_OVERLAY = document.getElementById('loading-overlay');
    UI_LOADING_STATUS = document.getElementById('loading-status');
    UI_PROCEED_BTN = document.getElementById('proceed-btn');

    const fg = ForceGraph3D({
        rendererConfig: {
            antialias: true, 
            powerPreference: 'high-performance',
            logarithmicDepthBuffer: false,
            precision: 'highp'
        }
    })(el)
        .graphData({ nodes: [], links: [] })
        .d3Force('charge', null)
        .cooldownTicks(0)
        .enableNodeDrag(false)
        .linkVisibility(() => false); //draw manually

    fg.scene().add(new THREE.AmbientLight(0xffffff, 0.7));
    const dirLight = new THREE.DirectionalLight(0xffffff, 0.5);
    dirLight.position.set(1, 1, 1).normalize();
    fg.scene().add(dirLight);

    const cam = fg.camera();
    cam.near = 0.1;
    cam.far = 1e9;
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
        controls.constraint.smoothZoom = true;
        controls.constraint.zoomDampingFactor = 0.2;
        controls.constraint.smoothZoomSpeed = 5.0;
    }

    let lastControlUpdate = 0;
    //60 fps
    const CONTROL_UPDATE_INTERVAL = 16; 
    controls.addEventListener('change', () => {
        const now = performance.now();
        if (now - lastControlUpdate > CONTROL_UPDATE_INTERVAL) {
            lastControlUpdate = now;
            if (ACTIVE_HIT) updateTooltipPosition(fg, cam);
        }
    });

    UI_BTN?.addEventListener('click', (e) => {
        e.preventDefault();
        const q = (UI_INPUT?.value ?? '').trim();
        if (!q) { setStatus(''); return; }
        focusNodeByName(q, fg, cam, controls);
    });
    UI_INPUT?.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            UI_BTN?.click();
        }
    });

    ensureTooltip(el);
    ensureHighlightMesh(fg.scene());

    // Get graph data
    const url = `${BACKEND_BASE}/data/graph?_cb=${Date.now()}`;
    fetch(url)
        .then(r => {
            if (!r.ok) throw new Error(`Network response was bad: ${r.statusText}`);
            UI_LOADING_STATUS.textContent = 'Parsing data';
            return r.json();
        })
        .then(data => {
            UI_LOADING_STATUS.textContent = 'Building visualization';

            let { nodes, links } = data;
            if (EDGE_CAP != null && links.length > EDGE_CAP) {
                links = links.slice(0, EDGE_CAP);
            }

            nodes.forEach(n => {
                n.fx = n.x; n.fy = n.y; n.fz = n.z ?? 0;
            });

            const counts = {};
            nodes.forEach(n => {
                const id = +n.community;
                counts[id] = (counts[id] || 0) + 1;
            });
            const topCommunities = Object.entries(counts)
                .sort((a, b) => b[1] - a[1])
                .slice(0, TOP_K)
                .map(([id]) => +id);

            setupInstancedNodes(fg, nodes, topCommunities);
            NAME_INDEX = buildNameIndex(nodes);
            fg.graphData({ nodes, links });
            addOptimizedBundledEdges(fg.scene(), links);

            const { center, diag } = computeBBox(nodes);
            controls.target.set(center.x, center.y, center.z);
            cam.position.set(center.x, center.y, center.z + diag * 0.8);
            cam.updateProjectionMatrix();

            installPicking(fg, cam);

            // Now show proceed
            UI_LOADING_STATUS.style.display = 'none';
            UI_PROCEED_BTN.style.display = 'inline-block';

            UI_PROCEED_BTN.addEventListener('click', () => {
                UI_LOADING_OVERLAY.style.opacity = '0';
                setTimeout(() => {
                    UI_LOADING_OVERLAY.style.display = 'none';
                }, 750);
                UI_WRAPPER.style.display = 'flex';
            });

        })
        .catch(err => {
            console.error(err);
            UI_LOADING_STATUS.textContent = `Error loading graph: ${err.message}`;
            UI_LOADING_STATUS.style.color = '#ff6b6b';
        });
});

// Instance nodes for performance
function setupInstancedNodes(fg, nodes, topCommunities) {
    const sphereGeoHigh = new THREE.SphereGeometry(SPHERE_RADIUS, 8, 8);
    const sphereGeoMed = new THREE.SphereGeometry(SPHERE_RADIUS, 6, 6);
    const sphereGeoLow = new THREE.SphereGeometry(SPHERE_RADIUS, 5, 5);

    const nodesByColor = {};
    nodes.forEach(node => {
        const comm = +node.community;
        const idx = topCommunities.indexOf(comm);
        const color = idx >= 0 ? PALETTE[idx % PALETTE.length] : FALLBACK_COLOR;
        (nodesByColor[color] ||= []).push(node);
    });

    Object.entries(nodesByColor).forEach(([color, colorNodes]) => {

        const material = new THREE.MeshStandardMaterial({
            color: +color,
            metalness: 0.3,
            roughness: 0.6
        });

        const lod = new THREE.LOD();
        const instancedHigh = new THREE.InstancedMesh(sphereGeoHigh, material, colorNodes.length);
        const instancedMed = new THREE.InstancedMesh(sphereGeoMed, material, colorNodes.length);
        const instancedLow = new THREE.InstancedMesh(sphereGeoLow, material, colorNodes.length);

        const m = new THREE.Matrix4();
        colorNodes.forEach((node, i) => {
            m.setPosition(node.x, node.y, node.z || 0);
            instancedHigh.setMatrixAt(i, m);
            instancedMed.setMatrixAt(i, m);
            instancedLow.setMatrixAt(i, m);
        });
        instancedHigh.instanceMatrix.needsUpdate = true;
        instancedMed.instanceMatrix.needsUpdate = true;
        instancedLow.instanceMatrix.needsUpdate = true;

        instancedHigh.userData.nodes = colorNodes;
        instancedMed.userData.nodes = colorNodes;
        instancedLow.userData.nodes = colorNodes;

        PICKABLE_MESHES.push(instancedHigh, instancedMed, instancedLow);

        lod.addLevel(instancedHigh, LOD_DISTANCES.high);
        lod.addLevel(instancedMed, LOD_DISTANCES.medium);
        lod.addLevel(instancedLow, LOD_DISTANCES.low);

        fg.scene().add(lod);
    });

    fg.nodeThreeObject(() => new THREE.Object3D());
}

// Should cull edges if they are far enough so everything is not loaded at once
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
            positions[w] = a.x; positions[w + 1] = a.y; positions[w + 2] = a.z;
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

    scene.add(lines);
}

// Able to click nodes and see information without huge amounts of performance hit
function installPicking(fg, cam) {
    const canvas = fg.renderer().domElement;
    const raycaster = new THREE.Raycaster();
    const mouse = new THREE.Vector2();

    raycaster.params.Mesh = { ...(raycaster.params.Mesh || {}), threshold: SPHERE_RADIUS * 0.4 };

    canvas.addEventListener('pointerdown', (evt) => {
        screenToNDC(evt, canvas, mouse);
        raycaster.setFromCamera(mouse, cam);
        const hits = raycaster.intersectObjects(PICKABLE_MESHES, false);

        if (!hits.length) {
            hideTooltip();
            highlightOff();
            return;
        }
        const hit = hits[0];
        const arr = hit.object.userData?.nodes;
        const node = (arr && hit.instanceId != null) ? arr[hit.instanceId] : null;

        if (node) {
            showTooltip(node, hit.point, fg, cam);
            highlightAt(node.x, node.y, node.z || 0);
            setStatus(node?.name || '');
        } else {
            hideTooltip();
            highlightOff();
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

function ensureHighlightMesh(scene) {
    if (HIGHLIGHT_MESH) return;
    //Increased segments for the highlighted sphere
    const geom = new THREE.SphereGeometry(SPHERE_RADIUS * 1.7, 32, 32);
    const mat = new THREE.MeshBasicMaterial({ color: 0xffffff, wireframe: true, depthTest: false, transparent: true, opacity: 0.9 });
    HIGHLIGHT_MESH = new THREE.Mesh(geom, mat);
    HIGHLIGHT_MESH.visible = false;
    HIGHLIGHT_MESH.renderOrder = 9999;
    scene.add(HIGHLIGHT_MESH);
}

function highlightAt(x, y, z) {
    if (!HIGHLIGHT_MESH) return;
    HIGHLIGHT_MESH.position.set(x, y, z);
    HIGHLIGHT_MESH.visible = true;
}
function highlightOff() {
    if (HIGHLIGHT_MESH) HIGHLIGHT_MESH.visible = false;
}

function screenToNDC(evt, canvas, outVec2) {
    const rect = canvas.getBoundingClientRect();
    const x = ((evt.clientX - rect.left) / rect.width) * 2 - 1;
    const y = -((evt.clientY - rect.top) / rect.height) * 2 + 1;
    outVec2.set(x, y);
}

function showTooltip(node, point, fg, cam) {
    ACTIVE_HIT = { node, point: point.clone() };
    const text = node?.[DISPLAY_FIELD] ?? '(unknown)';
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
    TOOLTIP_EL.style.left = `${(p.x * halfW) + halfW}px`;
    TOOLTIP_EL.style.top = `${(-p.y * halfH) + halfH}px`;
}

// Search function stuff - should switch to some non-exact matching later
function buildNameIndex(nodes) {
    const idx = new Map();
    for (const n of nodes) {
        const k = normalizeName(n?.name);
        if (k) idx.set(k, n);
    }
    return idx;
}

function normalizeName(s) {
    if (!s || typeof s !== 'string') return '';
    return s.trim().toLowerCase();
}

function setStatus(msg, ok = true) {
    if (!UI_STATUS) return;
    UI_STATUS.textContent = msg;
    UI_STATUS.style.color = ok ? '#d1ffd1' : '#ffd1d1';
}

function focusNodeByName(rawName, fg, cam, controls) {
    if (!NAME_INDEX) return;
    const key = normalizeName(rawName);
    const node = NAME_INDEX.get(key);
    if (!node) {
        setStatus('Not found', false);
        hideTooltip();
        highlightOff();
        return;
    }

    const target = new THREE.Vector3(node.x, node.y, node.z || 0);
    controls.target.copy(target);

    const camDir = new THREE.Vector3(0, 0, 1);
    const dist = SPHERE_RADIUS * 80;
    const newPos = target.clone().add(camDir.multiplyScalar(dist));
    cam.position.copy(newPos);
    cam.updateProjectionMatrix();

    showTooltip(node, target, fg, cam);
    highlightAt(target.x, target.y, target.z);

    setStatus(node.name || '');
}

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
    let minX = Infinity, maxX = -Infinity,
        minY = Infinity, maxY = -Infinity,
        minZ = Infinity, maxZ = -Infinity;

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


getPlaylistsBtn.addEventListener('click', async () => {
    const originalText = getPlaylistsBtn.textContent;
    getPlaylistsBtn.textContent = 'Loading...';
    getPlaylistsBtn.disabled = true;
    playlistContainer.innerHTML = ''; // Clear previous results

    try {
        const response = await fetch(`${BACKEND_BASE}/api/playlists`, { 
            credentials: 'include',
            method: 'GET',
            headers: {
                'Content-Type': 'application/json'
            }
        });

        if (!response.ok) {
            if (response.status === 401) {
                // User needs to authenticate
                const shouldLogin = confirm('You need to log in with Spotify to load playlists. Would you like to log in now?');
                if (shouldLogin) {
                    window.location.href = '/auth/login';
                    return;
                }
                playlistContainer.innerHTML = '<span style="color: #ffd1d1;">Authentication required</span>';
            } else {
                throw new Error(`Server responded with status: ${response.status}`);
            }
            return;
        }
    } catch (error) {
        console.error('Failed to fetch playlists:', error);
        playlistContainer.innerHTML = '<span style="color: #ffd1d1;">Error loading playlists</span>';
    } finally {
        getPlaylistsBtn.textContent = originalText;
        getPlaylistsBtn.disabled = false;
    }
});