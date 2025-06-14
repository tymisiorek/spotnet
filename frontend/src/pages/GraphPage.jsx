import { useMemo } from "react";
import ForceGraph3D from "react-force-graph-3d";

export default function GraphPage() {
  // build a random mini-graph once
  const graphData = useMemo(() => {
    const N = 100;
    const nodes = [...Array(N).keys()].map((i) => ({ id: i }));
    const links = [];
    for (let i = 0; i < N; i++) {
      for (let j = i + 1; j < N; j++) {
        if (Math.random() < 0.05) links.push({ source: i, target: j });
      }
    }
    if (!links.length) links.push({ source: 0, target: 1 });
    return { nodes, links };
  }, []);

  return (
    <ForceGraph3D
      graphData={graphData}
      backgroundColor="#000"       // black canvas
      nodeAutoColorBy="id"
      nodeLabel="id"
      width={window.innerWidth}
      height={window.innerHeight}
    />
  );
}
