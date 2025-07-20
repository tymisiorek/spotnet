import './assets/styles.css';
import './components/animations.js';

import * as THREE    from 'three';
import ForceGraph3D  from '3d-force-graph';

document.addEventListener('DOMContentLoaded', () => {
  const loginBtn = document.getElementById('login-btn');
  if (loginBtn) {
    loginBtn.addEventListener('click', () => {
      window.location.href = 'http://localhost:8000/auth/login';
    });
  }
});


document.addEventListener('DOMContentLoaded', () => {
  // …your existing login‑button logic…
  

  // graph init
  const container = document.getElementById('3d-graph');
  if (container) {
    ForceGraph3D()(container)
      .graphData({
        nodes: [{ id: 'A' }, { id: 'B' }, { id: 'C' }],
        links: [{ source: 'A', target: 'B' }, { source: 'B', target: 'C' }]
      })
      .cameraPosition({ z: 50 });
  }
});