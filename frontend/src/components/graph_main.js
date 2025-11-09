import * as THREE from 'three';
import {
  BACKEND_BASE, EDGE_CAP, TOP_K, state,
  initGraph, ensureTooltip, ensureHighlightMesh,
  setupInstancedNodes, addOptimizedBundledEdges,
  buildNameIndex, focusNodeByName, installPicking,
  computeBBox, setStatus, updateTooltipPosition
} from './graph.js';
import { loadPlaylists, fetchPlaylistArtistIds, resetHighlights, highlightArtistsInGraph } from './playlists.js';

document.addEventListener('DOMContentLoaded', () => {
  const loginBtn = document.getElementById('loginBtn');
  if (loginBtn) loginBtn.addEventListener('click', () => (window.location.href = '/auth/login'));

  const el = document.getElementById('3d-graph');
  if (!el) return;
  state.UI.WRAPPER = document.getElementById('ui');
  state.UI.INPUT = document.getElementById('searchInput');
  state.UI.BTN = document.getElementById('searchBtn');
  state.UI.STATUS = document.getElementById('status-notification');
  state.UI.LOADING_OVERLAY = document.getElementById('loading-overlay');
  state.UI.LOADING_STATUS = document.getElementById('loading-status');
  state.UI.PROCEED_BTN = document.getElementById('proceed-btn');
  state.UI.PLAYLIST_DROPDOWN = document.getElementById('playlistDropdown');
  state.UI.HIGHLIGHT_ALL_BTN = document.getElementById('highlightAllBtn');

  // init 3D
  window.theGraph = initGraph(el); 
  ensureTooltip(el);
  ensureHighlightMesh();

  // smoother tooltip position on controls change
  const controls = state.theGraph.controls();
  let last = 0;
  controls.addEventListener('change', () => {
    const now = performance.now();
    if (now - last > 16) { last = now; if (state.ACTIVE_HIT) updateTooltipPosition(); }
  });

  // search wiring
  state.UI.BTN?.addEventListener('click', (e) => {
    e.preventDefault();
    const q = (state.UI.INPUT?.value ?? '').trim();
    if (!q) return;
    focusNodeByName(q);
  });
  state.UI.INPUT?.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); state.UI.BTN?.click(); } });

  // fetch data
  const url = `${BACKEND_BASE}/data/graph?_cb=${Date.now()}`;
  fetch(url)
    .then(r => { if (!r.ok) throw new Error(`Network response was bad: ${r.statusText}`); state.UI.LOADING_STATUS.textContent = 'Parsing data'; return r.json(); })
    .then(data => {
      state.UI.LOADING_STATUS.textContent = 'Building visualization';
      let { nodes, links } = data;
      state.graphData = { nodes, links };
      if (EDGE_CAP!=null && links.length>EDGE_CAP) links = links.slice(0, EDGE_CAP);
      nodes.forEach(n => { n.fx=n.x; n.fy=n.y; n.fz=n.z ?? 0; });

      //spread out graph:
      // --- Uniform scaling to spread out the graph while preserving proportions ---
      const { center, diag } = computeBBox(nodes);
      const scale_factor = 3.0; // adjust this (2–5x typical)

      for (const n of nodes) {
        n.x = center.x + (n.x - center.x) * scale_factor;
        n.y = center.y + (n.y - center.y) * scale_factor;
        n.z = center.z + (n.z - center.z) * scale_factor;
      }

      // Scale edge-bundled coordinates too (if they exist)
      for (const e of links) {
        if (typeof e.points === 'string' && e.points.includes(',')) {
          const pts = e.points.split('|').map(s => {
            const [xs, ys, zs] = s.split(',').map(Number);
            const x = center.x + (xs - center.x) * scale_factor;
            const y = center.y + (ys - center.y) * scale_factor;
            const z = center.z + (zs - center.z) * scale_factor;
            return `${x},${y},${z}`;
          });
          e.points = pts.join('|');
        }
      }


      const counts = {}; nodes.forEach(n => { const id=+n.community; counts[id]=(counts[id]||0)+1; });
      const topCommunities = Object.entries(counts).sort((a,b)=>b[1]-a[1]).slice(0,TOP_K).map(([id])=>+id);

      setupInstancedNodes(nodes, topCommunities);
      buildNameIndex(nodes);
      state.theGraph.graphData({ nodes, links });
      addOptimizedBundledEdges(links);

      // const { center, diag } = computeBBox(nodes);
      const cam = state.theGraph.camera();
      controls.target.set(center.x, center.y, center.z);
      cam.position.set(center.x, center.y, center.z + diag*0.8);
      cam.updateProjectionMatrix();

      installPicking();

      state.UI.LOADING_STATUS.style.display = 'none';
      state.UI.PROCEED_BTN.style.display = 'inline-block';
      state.UI.PROCEED_BTN.addEventListener('click', () => {
        state.UI.LOADING_OVERLAY.style.opacity = '0';
        setTimeout(() => { state.UI.LOADING_OVERLAY.style.display = 'none'; }, 750);
        state.UI.WRAPPER.style.display = 'flex';
        loadPlaylists();
      });
    })
    .catch(err => {
      console.error(err);
      state.UI.LOADING_STATUS.textContent = `Error loading graph: ${err.message}`;
      state.UI.LOADING_STATUS.style.color = '#ff6b6b';
    });

  // playlists UI events
  state.UI.PLAYLIST_DROPDOWN.addEventListener('change', async (e) => {
    const playlistId = e.target.value;
    if (!playlistId) { state.selectedPlaylistArtists = []; resetHighlights(); return; }
    setStatus(`Fetching artists for playlist...`, true);
    try {
      const ids = await fetchPlaylistArtistIds(playlistId);
      state.selectedPlaylistArtists = ids;
      highlightArtistsInGraph(ids);
      setStatus(`Highlighted ${ids.length} artists.`, true);
    } catch (err) {
      console.error(err); setStatus('Error highlighting artists.', false);
      state.selectedPlaylistArtists = []; resetHighlights();
    }
  });

  state.UI.HIGHLIGHT_ALL_BTN.addEventListener('click', async () => {
    if (!state.userPlaylists?.length) { setStatus('Please load playlists first.', false); return; }
    const btn = state.UI.HIGHLIGHT_ALL_BTN, ddl = state.UI.PLAYLIST_DROPDOWN;
    btn.disabled = true; ddl.disabled = true; setStatus(`Fetching artists from ${state.userPlaylists.length} playlists...`, true);
    try {
      const arrays = await Promise.all(state.userPlaylists.map(p =>
        fetch(`${BACKEND_BASE}/api/playlist/${p.id}/artists`, { credentials:'include' })
          .then(res => (res.ok ? res.json() : []))
      ));
      const unique = new Map();
      arrays.flat().forEach(a => { if (a?.id && !unique.has(a.id)) unique.set(a.id, a); });
      const ids = Array.from(unique.values()).map(a=>a.id);
      highlightArtistsInGraph(ids);
      setStatus(`Highlighted ${ids.length} total unique artists.`, true);
    } catch (err) {
      console.error(err); setStatus('An error occurred while highlighting all artists.', false);
      resetHighlights();
    } finally {
      btn.disabled = false; ddl.disabled = false;
    }
  });
});
