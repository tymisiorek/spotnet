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
