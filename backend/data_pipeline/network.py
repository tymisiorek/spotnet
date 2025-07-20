#!/usr/bin/env python3
import os
import math
import csv
import json
import random
from pathlib import Path
from dotenv import load_dotenv, find_dotenv

# ---------- Hyperparameters ----------
NODE_TARGET    = -1            # include all nodes
SAMPLING_MODE  = "none"        # "grid" | "random" | "none"
MAX_PER_CELL   = 200           # only if grid sampling
EDGE_FACTOR    = 3.0           # ignored when MAX_EDGES < 0
DEGREE_CAP     = 0             # unlimited
EDGE_MODE      = "random"
INCLUDE_EDGES  = True
SHUFFLE_NODES  = False
MAX_EDGES      = -1            # <0 => include every edge
SEED           = 1337          # deterministic randomness

# Terrain (community‑terraced + noise bump)
NOISE_SCALE    = 20.0
NOISE_FREQ     = 1.5
Z_RANGE        = 400.0         # total height span across communities

# Galaxy layout params
GALAXY_ARMS    = 4             # number of spiral arms
TWIST          = 2.0           # spiral tightness
RADIAL_JITTER  = 0.05          # ±5% radial noise
DEGREE_BIAS    = 0.4           # how strongly hubs pull inward
ARM_DISK_RATIO = 0.7           # 70% of nodes on arms, 30% in disk

# Paths
load_dotenv(find_dotenv())
ROOT_DIR    = os.getenv("ROOT_DIR", str(Path(__file__).parent.parent))
DATA_DIR    = Path(ROOT_DIR) / "data"
NODES_CSV   = DATA_DIR / "network_nodes.csv"
EDGES_CSV   = DATA_DIR / "network_edges.csv"
OUTPUT_JSON = DATA_DIR / "graph.json"

random.seed(SEED)

# ---------- 2D Value Noise (for gentle terrain bumps) ----------
def _hash_int(i, j):
    n = (i * 1836311903) ^ (j * 2971215073) ^ SEED
    n ^= (n >> 13)
    n *= 1274126177
    return n & 0xffffffff

def _rand_unit(i, j):
    return _hash_int(i, j) / 0xffffffff

def _fade(t):
    return t * t * t * (t * (t * 6 - 15) + 10)

def value_noise(x, y):
    xi, yi = int(math.floor(x)), int(math.floor(y))
    xf, yf = x - xi, y - yi
    u, v = _fade(xf), _fade(yf)
    v00 = _rand_unit(xi, yi)
    v10 = _rand_unit(xi+1, yi)
    v01 = _rand_unit(xi, yi+1)
    v11 = _rand_unit(xi+1, yi+1)
    a = v00 + (v10 - v00)*u
    b = v01 + (v11 - v01)*u
    return a + (b - a)*v  # in [0,1]

# ---------- Build Graph with Galaxy Layout ----------
def build_graph():
    # 1) Load nodes
    nodes = []
    with open(NODES_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                row["x"], row["y"] = float(row["x"]), float(row["y"])
            except (KeyError, ValueError):
                continue
            nodes.append(row)
    total_nodes = len(nodes)
    if total_nodes == 0:
        raise RuntimeError("No nodes found in CSV")

    # Compute original spatial bounds
    xs = [n["x"] for n in nodes]
    ys = [n["y"] for n in nodes]
    minX, maxX = min(xs), max(xs)
    minY, maxY = min(ys), max(ys)
    rangeX = maxX - minX or 1.0
    rangeY = maxY - minY or 1.0

    # 2) Node sampling (here, all nodes)
    selected_nodes = list(nodes)
    served_nodes = len(selected_nodes)
    selected_ids = {n["id"] for n in selected_nodes}

    # 3) Edge sampling (all edges)
    served_edges = []
    total_edges_file = 0
    if INCLUDE_EDGES:
        with open(EDGES_CSV, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                total_edges_file += 1
                s, t = row.get("source"), row.get("target")
                if s in selected_ids and t in selected_ids:
                    served_edges.append({"source": s, "target": t})

    # 4) Community detection via Leiden (~30 clusters)
    try:
        import igraph as ig
    except ImportError:
        raise RuntimeError("igraph required: pip install igraph")
    id_to_index = {n["id"]: i for i, n in enumerate(selected_nodes)}
    g_obj = ig.Graph()
    g_obj.add_vertices(served_nodes)
    g_obj.add_edges([
        (id_to_index[e["source"]], id_to_index[e["target"]])
        for e in served_edges
    ])
    part = g_obj.community_leiden(resolution=0.05)  # ~30 communities
    membership = part.membership
    num_comms  = max(membership) + 1
    degrees    = g_obj.degree()
    max_deg    = max(degrees) or 1

    # 5) Galaxy layout: override x,y, assign arm
    #    R_MAX set to half the original bounding diagonal
    diag = math.hypot(rangeX, rangeY)
    R_MAX = diag / 2.0

    for i, n in enumerate(selected_nodes):
        dn = degrees[i] / max_deg
        # choose an arm index for every node
        arm = random.randrange(GALAXY_ARMS)
        n["arm"] = arm

        # mix arm vs disk
        if random.random() < ARM_DISK_RATIO:
            # ARM mode: along spiral
            u = random.random()  # uniform [0,1]
            r_base = (u**0.5) * R_MAX
            r = r_base * (1.0 - dn * DEGREE_BIAS)
            theta0 = (2 * math.pi * arm) / GALAXY_ARMS
            theta = theta0 + TWIST * (r / R_MAX) + random.uniform(-0.1, 0.1)
        else:
            # DISK mode: uniform background
            u = random.random()
            r_base = (u**0.5) * R_MAX
            r = r_base * (1.0 - dn * DEGREE_BIAS)
            theta = random.random() * 2 * math.pi

        # small radial jitter
        r += random.uniform(-RADIAL_JITTER*R_MAX, RADIAL_JITTER*R_MAX)

        # finalize coordinates
        n["x"] = r * math.cos(theta)
        n["y"] = r * math.sin(theta)

    # 6) Terraced Z assignment (community bands + noise bump)
    for i, n in enumerate(selected_nodes):
        if num_comms > 1:
            base_h = (membership[i] / (num_comms - 1)) * Z_RANGE
        else:
            base_h = Z_RANGE / 2.0

        # normalize new x,y into [0,1] for noise
        nx = (n["x"] + R_MAX) / (2 * R_MAX)
        ny = (n["y"] + R_MAX) / (2 * R_MAX)
        bump = (value_noise(nx * NOISE_FREQ, ny * NOISE_FREQ) - 0.5) * NOISE_SCALE

        n["community"] = membership[i]
        n["z"]         = base_h + bump

    # 7) Output JSON
    result = {
        "nodes": selected_nodes,
        "links": served_edges,
        "meta": {
            "total_nodes":   total_nodes,
            "served_nodes":  served_nodes,
            "total_edges":   total_edges_file,
            "served_edges":  len(served_edges),
            "communities":   num_comms,
            "z_range":       Z_RANGE,
            "noise_scale":   NOISE_SCALE,
            "arms":          GALAXY_ARMS
        }
    }
    return result

if __name__ == "__main__":
    graph = build_graph()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(graph, f)
    print(f"Wrote graph with {len(graph['nodes'])} nodes, "
          f"{len(graph['links'])} edges, "
          f"{graph['meta']['communities']} communities")
