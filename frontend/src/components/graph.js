// frontend/src/graph-page.js

import * as THREE      from 'three';
import ForceGraph3D    from '3d-force-graph';

// wait for the DOM so we know #3d-graph exists
document.addEventListener('DOMContentLoaded', () => {
  const container = document.getElementById('3d-graph');
  if (!container) return;

  // build a simple 3‑node chain: A → B → C
  ForceGraph3D()(container)
    .graphData({
      nodes: [
        { id: 'A' },
        { id: 'B' },
        { id: 'C' }
      ],
      links: [
        { source: 'A', target: 'B' },
        { source: 'B', target: 'C' }
      ]
    })
    // pull the camera back so the nodes are visible
    .cameraPosition({ z: 50 });
});
