import os
import math
import csv
import json
import random
from pathlib import Path
from dotenv import load_dotenv, find_dotenv
import igraph as ig

NODE_TARGET = -1
SAMPLING_MODE = "none"
MAX_PER_CELL = 200
EDGE_FACTOR = 3.0
DEGREE_CAP = 0
EDGE_MODE = "random"
INCLUDE_EDGES = True
SHUFFLE_NODES = False
MAX_EDGES = -1
SEED = 112

NOISE_SCALE = 20.0
NOISE_FREQ = 1.5
Z_RANGE = 400.0

GALAXY_ARMS = 4
TWIST = 2.0
RADIAL_JITTER = 0.05
DEGREE_BIAS = 0.4
ARM_DISK_RATIO = 0.7

load_dotenv(find_dotenv())
ROOT_DIR = os.getenv("ROOT_DIR", f"{Path(__file__).parent.parent}")
DATA_DIR = Path(f"{ROOT_DIR}/data")
NODES_CSV = DATA_DIR / "network_nodes.csv"
EDGES_CSV = DATA_DIR / "network_edges.csv"
OUTPUT_JSON = DATA_DIR / "graph.json"
random.seed(SEED)

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
    v10 = _rand_unit(xi + 1, yi)
    v01 = _rand_unit(xi, yi + 1)
    v11 = _rand_unit(xi + 1, yi + 1)
    a = v00 + (v10 - v00) * u
    b = v01 + (v11 - v01) * u
    return a + (b - a) * v

def build_graph():
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
        raise RuntimeError("no nodes found in csv")

    xs = [n["x"] for n in nodes]
    ys = [n["y"] for n in nodes]
    minX, maxX = min(xs), max(xs)
    minY, maxY = min(ys), max(ys)
    rangeX = maxX - minX or 1
    rangeY = maxY - minY or 1
    diag = math.hypot(rangeX, rangeY)
    R_MAX = diag / 2

    selected_nodes = list(nodes)
    selected_ids = {n["id"] for n in selected_nodes}

    served_edges = []
    total_edges_file = 0
    if INCLUDE_EDGES:
        with open(EDGES_CSV, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                total_edges_file += 1
                s, t = row["source"], row["target"]
                if s in selected_ids and t in selected_ids:
                    served_edges.append({"source": s, "target": t})

    served_nodes = len(selected_nodes)
    id_to_index = {n["id"]: i for i, n in enumerate(selected_nodes)}

    # build igraph, attach spatial coords
    g = ig.Graph()
    g.add_vertices(served_nodes)
    edge_tuples = [
        (id_to_index[e["source"]], id_to_index[e["target"]])
        for e in served_edges
    ]
    g.add_edges(edge_tuples)
    # x,y,z will get set below
    # assign galaxy layout
    degrees = g.degree()
    max_deg = max(degrees) or 1
    for i, n in enumerate(selected_nodes):
        dn = degrees[i] / max_deg
        arm = random.randrange(GALAXY_ARMS)
        n["arm"] = arm
        if random.random() < ARM_DISK_RATIO:
            u = random.random()
            r_base = (u**0.5) * R_MAX
            r = r_base * (1 - dn * DEGREE_BIAS)
            theta0 = (2 * math.pi * arm) / GALAXY_ARMS
            theta = theta0 + TWIST * (r / R_MAX) + random.uniform(-0.1, 0.1)
        else:
            u = random.random()
            r_base = (u**0.5) * R_MAX
            r = r_base * (1 - dn * DEGREE_BIAS)
            theta = random.random() * 2 * math.pi

        r += random.uniform(-RADIAL_JITTER * R_MAX,
                            RADIAL_JITTER * R_MAX)
        x = r * math.cos(theta)
        y = r * math.sin(theta)
        n["x"], n["y"] = x, y

    # compute z with terraced communities + noise
    part = g.community_leiden(resolution=0.05)
    membership = part.membership
    num_comms = max(membership) + 1
    for i, n in enumerate(selected_nodes):
        if num_comms > 1:
            base_h = (membership[i] / (num_comms - 1)) * Z_RANGE
        else:
            base_h = Z_RANGE / 2
        nx = (n["x"] + R_MAX) / (2 * R_MAX)
        ny = (n["y"] + R_MAX) / (2 * R_MAX)
        bump = (value_noise(nx * NOISE_FREQ,
                             ny * NOISE_FREQ) - 0.5) * NOISE_SCALE
        n["community"] = membership[i]
        n["z"] = base_h + bump

    # attach coords as vertex attributes
    xs = [n["x"] for n in selected_nodes]
    ys = [n["y"] for n in selected_nodes]
    zs = [n["z"] for n in selected_nodes]
    g.vs["x"], g.vs["y"], g.vs["z"] = xs, ys, zs

    # build skeleton = MST over spatial distances
    # first compute edge weights = 3D Euclidean
    weights = []
    for u, v in g.get_edgelist():
        dx = xs[u] - xs[v]
        dy = ys[u] - ys[v]
        dz = zs[u] - zs[v]
        weights.append(math.sqrt(dx * dx + dy * dy + dz * dz))
    g.es["weight"] = weights
    mst = g.spanning_tree(weights=weights)

    # for each original link, find the path in mst and record its coords
    for e, edge in enumerate(served_edges):
        u = id_to_index[edge["source"]]
        v = id_to_index[edge["target"]]
        path = mst.get_shortest_paths(u, to=v, weights=None)[0]
        bundle = [[xs[idx], ys[idx], zs[idx]] for idx in path]
        edge["bundle_points"] = bundle

    result = {
        "nodes": selected_nodes,
        "links": served_edges,
        "meta": {
            "total_nodes": total_nodes,
            "served_nodes": served_nodes,
            "total_edges": total_edges_file,
            "served_edges": len(served_edges),
            "communities": num_comms,
            "z_range": Z_RANGE,
            "noise_scale": NOISE_SCALE,
            "arms": GALAXY_ARMS
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
