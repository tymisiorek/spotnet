import { state, setStatus, BACKEND_BASE, parsePoints, SPHERE_RADIUS, hideInstancedNodes, showInstancedNodes } from './graph.js';
import * as THREE from 'three';

function ensureCache() {
  if (!state._playlistArtistsCache) state._playlistArtistsCache = new Map();
  if (!state._allArtistsIdsCached) state._allArtistsIdsCached = null;
  if (!state._playlistsSignature) state._playlistsSignature = '';
}
function signatureOfPlaylists(playlists) {
  return (playlists || []).map(p => p.id).sort().join(',');
}
function invalidateAllCaches() {
  ensureCache();
  state._playlistArtistsCache.clear();
  state._allArtistsIdsCached = null;
}

export async function loadPlaylists() {
  const ddl = state.UI.PLAYLIST_DROPDOWN;
  const btn = state.UI.HIGHLIGHT_ALL_BTN;

  setStatus('Fetching your playlists...', true);

  try {
    const res = await fetch(`${BACKEND_BASE}/api/playlists`, { credentials: 'include' });
    if (!res.ok) {
      if (res.status === 401) {
        if (confirm('You need to log in with Spotify to load playlists. Log in now?')) {
          window.location.href = '/auth/login';
        }
      }
      setStatus('Authentication required to load playlists.', false);
      ddl.innerHTML = `<option value="">Login to see playlists</option>`;
      ddl.style.display = 'inline-block';
      return;
    }

    const playlists = await res.json();
    const newSig = signatureOfPlaylists(playlists);

    // Cache invalidation when playlist set changes
    ensureCache();
    if (newSig !== state._playlistsSignature) {
      invalidateAllCaches();
      state._playlistsSignature = newSig;
    }

    state.userPlaylists = playlists;

    ddl.innerHTML = '<option value="">Select a Playlist</option>';
    playlists.forEach(p => {
      const opt = document.createElement('option');
      opt.value = p.id;
      opt.textContent = p.name;
      ddl.appendChild(opt);
    });

    ddl.style.display = 'inline-block';
    btn.style.display = 'inline-block';

    // Initialize toggle
    btn.dataset.on = 'false';
    btn.textContent = 'Highlight All Artists';
    btn.setAttribute('aria-pressed', 'false');

    // Rebind
    btn.replaceWith(btn.cloneNode(true));
    state.UI.HIGHLIGHT_ALL_BTN = document.getElementById('highlightAllBtn');
    state.UI.HIGHLIGHT_ALL_BTN.addEventListener('click', toggleHighlightAll);

    setStatus('Playlists loaded successfully.', true);
  } catch (e) {
    console.error(e);
    setStatus('Error loading playlists.', false);
    ddl.innerHTML = `<option value="">Error loading playlists</option>`;
    ddl.style.display = 'inline-block';
  }
}

export async function fetchPlaylistArtistIds(playlistId) {
  ensureCache();
  if (state._playlistArtistsCache.has(playlistId)) {
    return state._playlistArtistsCache.get(playlistId);
  }
  const r = await fetch(`${BACKEND_BASE}/api/playlist/${playlistId}/artists`, { credentials: 'include' });
  if (!r.ok) throw new Error(`Server error: ${r.status}`);
  const artists = await r.json();
  const ids = artists.map(a => a.id);
  state._playlistArtistsCache.set(playlistId, ids);
  state._allArtistsIdsCached = null; 
  return ids;
}


export async function fetchAllPlaylistArtistIds() {
  ensureCache();

  if (!state.userPlaylists?.length) return [];
  if (state._allArtistsIdsCached) return state._allArtistsIdsCached;

  // Fetch only missing playlists
  const toFetch = [];
  for (const p of state.userPlaylists) {
    if (!state._playlistArtistsCache.has(p.id)) toFetch.push(p.id);
  }

  if (toFetch.length) {
    const results = await Promise.all(
      toFetch.map(pid =>
        fetch(`${BACKEND_BASE}/api/playlist/${pid}/artists`, { credentials: 'include' })
          .then(res => (res.ok ? res.json() : []))
          .catch(() => [])
          .then(arr => {
            const ids = (arr || []).map(a => a.id).filter(Boolean);
            state._playlistArtistsCache.set(pid, ids);
            return ids;
          })
      )
    );
  }

  // Build union from all per-playlist caches
  const union = new Set();
  for (const ids of state._playlistArtistsCache.values()) {
    for (const id of ids) union.add(id);
  }
  state._allArtistsIdsCached = Array.from(union);
  return state._allArtistsIdsCached;
}

export function resetHighlights() {
  state.highlightObjects.forEach(obj => {
    obj.geometry?.dispose?.(); obj.material?.dispose?.();
    state.theGraph.scene().remove(obj);
  });
  state.highlightObjects = [];
  showInstancedNodes();
}

export function highlightArtistsInGraph(artistIds) {
  resetHighlights();
  if (!artistIds?.length) return;

  const set = new Set(artistIds);
  const matched = [], unmatched = [];
  state.graphData.nodes.forEach(n => (set.has(n.id) ? matched : unmatched).push(n));
  const highlightedLinks = state.graphData.links.filter(l =>
    set.has(l.source.id || l.source) && set.has(l.target.id || l.target)
  );

  hideInstancedNodes();

  const sphereGeo = new THREE.SphereGeometry(SPHERE_RADIUS, 5, 5);
  const dimMat = new THREE.MeshStandardMaterial({ color: 0x222222, metalness: 0.1, roughness: 0.8 });
  const m = new THREE.Matrix4();

  const unmatchedMesh = new THREE.InstancedMesh(sphereGeo, dimMat, unmatched.length);
  unmatched.forEach((n, i) => { m.setPosition(n.x, n.y, n.z || 0); unmatchedMesh.setMatrixAt(i, m); });
  unmatchedMesh.instanceMatrix.needsUpdate = true;
  state.theGraph.scene().add(unmatchedMesh); state.highlightObjects.push(unmatchedMesh);

  const hiMat = new THREE.MeshStandardMaterial({ color: 0xffff00, metalness: 0.3, roughness: 0.4, emissive: 0x333300 });
  const matchedMesh = new THREE.InstancedMesh(sphereGeo, hiMat, matched.length);
  matched.forEach((n, i) => { m.setPosition(n.x, n.y, n.z || 0); matchedMesh.setMatrixAt(i, m); });
  matchedMesh.instanceMatrix.needsUpdate = true;
  state.theGraph.scene().add(matchedMesh); state.highlightObjects.push(matchedMesh);

  if (highlightedLinks.length) {
    let segCount = 0;
    highlightedLinks.forEach(e => { const pts = parsePoints(e.points); if (pts && pts.length > 1) segCount += pts.length - 1; });
    const positions = new Float32Array(segCount * 2 * 3); let w = 0;
    highlightedLinks.forEach(e => {
      const pts = parsePoints(e.points); if (!pts || pts.length < 2) return;
      for (let i = 0; i < pts.length - 1; i++) {
        positions[w] = pts[i].x; positions[w + 1] = pts[i].y; positions[w + 2] = pts[i].z;
        positions[w + 3] = pts[i + 1].x; positions[w + 4] = pts[i + 1].y; positions[w + 5] = pts[i + 1].z;
        w += 6;
      }
    });
    const geom = new THREE.BufferGeometry();
    geom.setAttribute('position', new THREE.BufferAttribute(positions.slice(0, w), 3));
    const lines = new THREE.LineSegments(geom, new THREE.LineBasicMaterial({ color: 0xffff00, transparent: true, opacity: 0.5 }));
    state.theGraph.scene().add(lines); state.highlightObjects.push(lines);
  }
}

// ---------- toggle using cache ----------
export async function toggleHighlightAll() {
  const btn = state.UI.HIGHLIGHT_ALL_BTN;
  const ddl = state.UI.PLAYLIST_DROPDOWN;
  const isOn = btn.dataset.on === 'true';

  if (isOn) {
    btn.disabled = true;
    try {
      resetHighlights();
      btn.dataset.on = 'false';
      btn.textContent = 'Highlight All Artists';
      btn.setAttribute('aria-pressed', 'false');
      setStatus('Cleared highlights.', true);
    } finally {
      btn.disabled = false;
      ddl.disabled = false;
    }
    return;
  }

  if (!state.userPlaylists?.length) {
    setStatus('Please load playlists first.', false);
    return;
  }

  btn.disabled = true;
  ddl.disabled = true;

  const usingCache = !!state._allArtistsIdsCached;
  setStatus(
    usingCache
      ? 'Using cached artists…'
      : `Fetching artists from ${state.userPlaylists.length} playlists...`,
    true
  );

  try {
    const ids = await fetchAllPlaylistArtistIds();
    highlightArtistsInGraph(ids);
    btn.dataset.on = 'true';
    btn.textContent = 'Clear Highlight';
    btn.setAttribute('aria-pressed', 'true');
    setStatus(`Highlighted ${ids.length} total unique artists.`, true);
  } catch (err) {
    console.error(err);
    setStatus('An error occurred while highlighting all artists.', false);
    resetHighlights();
    btn.dataset.on = 'false';
    btn.textContent = 'Highlight All Artists';
    btn.setAttribute('aria-pressed', 'false');
  } finally {
    btn.disabled = false;
    ddl.disabled = false;
  }
}
