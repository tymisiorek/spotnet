import os
import math
import csv
import json
import random
from pathlib import Path
from dotenv import load_dotenv
from flask import Flask, request, jsonify, redirect
from flask_cors import CORS
from spotipy.oauth2 import SpotifyOAuth

# OPTIONAL: enable gzip (pip install flask-compress)
# from flask_compress import Compress

# ---------- Load .env ----------
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    load_dotenv(env_path)

# ---------- Adjustable Defaults (centralized) ----------
# Raise these (or override via query params) to push more data.
DEFAULT_NODE_TARGET   = int(os.getenv("DEFAULT_NODE_TARGET", "10000"))
DEFAULT_SAMPLING_MODE = os.getenv("DEFAULT_SAMPLING_MODE", "grid")  # grid|random|none
DEFAULT_EDGE_FACTOR   = float(os.getenv("DEFAULT_EDGE_FACTOR", "1.5"))
DEFAULT_MAX_PER_CELL  = int(os.getenv("DEFAULT_MAX_PER_CELL", "5"))
DEFAULT_DEGREE_CAP    = int(os.getenv("DEFAULT_DEGREE_CAP", "8"))
DEFAULT_EDGE_MODE     = os.getenv("DEFAULT_EDGE_MODE", "random")    # random|sequential

# ---------- Basic Config ----------
BACKEND_HOST         = os.getenv("BACKEND_HOST", "127.0.0.1")
BACKEND_PORT         = int(os.getenv("BACKEND_PORT", "8000"))
FRONTEND_ORIGIN      = (os.getenv("FRONTEND_ORIGIN", "") or "").rstrip("/")
SPOTIFY_CLIENT_ID    = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET= os.getenv("SPOTIFY_CLIENT_SECRET")
SPOTIFY_REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI")

STATIC_DIR = Path(__file__).parent.parent / "frontend" / (
    "dist"
    if (FRONTEND_ORIGIN == "" and (Path(__file__).parent.parent / "frontend" / "dist").exists())
    else "public"
)

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="")
# Compress(app)  # uncomment if using flask-compress

cors_origins = [FRONTEND_ORIGIN] if FRONTEND_ORIGIN else []
cors_resources = {
    r"/auth/*": {"origins": cors_origins or ["http://localhost:5173"]},
    r"/data/*": {"origins": "*"}
}
CORS(app, resources=cors_resources, methods=["GET", "POST"], allow_headers="*")

# ---------- Spotify OAuth ----------
sp_oauth = SpotifyOAuth(
    client_id=SPOTIFY_CLIENT_ID,
    client_secret=SPOTIFY_CLIENT_SECRET,
    redirect_uri=SPOTIFY_REDIRECT_URI,
    scope="user-library-read"
)

def frontend_url(path: str) -> str:
    if not path.startswith("/"):
        path = "/" + path
    return (FRONTEND_ORIGIN + path) if FRONTEND_ORIGIN else path

@app.route("/ping")
def ping():
    return jsonify(pong=True)

@app.route("/auth/login")
def login():
    return redirect(sp_oauth.get_authorize_url())

@app.route("/callback")
def callback():
    code = request.args.get("code")
    if not code:
        return jsonify(error="no code in request"), 400
    _token_info = sp_oauth.get_access_token(code)
    return redirect(frontend_url("/blank.html"))

@app.route("/api/me")
def api_me():
    return jsonify(status="ok")

# ---------- Data Paths ----------
DATA_DIR  = Path(__file__).parent.parent / "data"
NODES_CSV = DATA_DIR / "network_nodes.csv"
EDGES_CSV = DATA_DIR / "network_edges.csv"

# ---------- Helpers ----------
def parse_int(name, default):
    v = request.args.get(name, None)
    if v is None:
        return default
    if isinstance(v, str) and v.lower() in ("all", "none"):
        # caller can use nodes=all or degree_cap=none
        return -1 if name == "nodes" else 0
    try:
        return int(v)
    except ValueError:
        return default

def parse_float(name, default):
    v = request.args.get(name, None)
    if v is None:
        return default
    try:
        return float(v)
    except ValueError:
        return default

# ---------- Main Graph Endpoint ----------
@app.route("/data/graph")
def data_graph():
    """
    Query params (all optional):
      nodes=INT|all      desired node count; -1, 0, 'all' or >= total => all nodes
      sampling=grid|random|none
      max_per_cell=INT   per cell retention (grid)
      edge_factor=FLOAT  edges target = edge_factor * served_nodes (ignored if max_edges set)
      max_edges=INT      absolute cap (overrides edge_factor, -1 or 'all' => no cap besides degree caps)
      degree_cap=INT|none  per-node post-sampling cap (0 or 'none' => unlimited)
      edge_mode=random|sequential  affects order accepted
      include_edges=0|1
      shuffle_nodes=0|1  randomize order of selected_nodes (helps visual distribution)
    """
    # --- Parameters ---
    target_param  = request.args.get("nodes", str(DEFAULT_NODE_TARGET))
    sampling_mode = request.args.get("sampling", DEFAULT_SAMPLING_MODE)
    include_edges = request.args.get("include_edges", "1") == "1"
    edge_mode     = request.args.get("edge_mode", DEFAULT_EDGE_MODE)
    shuffle_nodes = request.args.get("shuffle_nodes", "0") == "1"

    # Numeric
    target        = parse_int("nodes", DEFAULT_NODE_TARGET)
    max_per_cell  = parse_int("max_per_cell", DEFAULT_MAX_PER_CELL)
    degree_cap    = parse_int("degree_cap", DEFAULT_DEGREE_CAP)
    edge_factor   = parse_float("edge_factor", DEFAULT_EDGE_FACTOR)
    max_edges_in  = request.args.get("max_edges", None)
    if max_edges_in is not None and max_edges_in.lower() in ("all", "none"):
        max_edges = -1
    else:
        max_edges = parse_int("max_edges", -999999) if max_edges_in is not None else None
        if max_edges == -999999:  # sentinel meaning not provided
            max_edges = None

    # --- Load nodes ---
    nodes = []
    with open(NODES_CSV, newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            try:
                row["x"] = float(row["x"])
                row["y"] = float(row["y"])
            except (KeyError, ValueError):
                continue
            nodes.append(row)

    total_nodes = len(nodes)
    if total_nodes == 0:
        return jsonify(error="no nodes"), 500

    # Bounds
    xs = [n["x"] for n in nodes]
    ys = [n["y"] for n in nodes]
    minX, maxX = min(xs), max(xs)
    minY, maxY = min(ys), max(ys)
    rangeX = maxX - minX or 1.0
    rangeY = maxY - minY or 1.0

    # Decide full vs sampled
    select_all = (sampling_mode == "none") or (target <= 0) or (target >= total_nodes)

    if select_all:
        selected_nodes = nodes
        buckets = {}
        cellSizeX = cellSizeY = None
    elif sampling_mode == "random":
        selected_nodes = random.sample(nodes, target) if target < total_nodes else nodes
        buckets = {}
        cellSizeX = cellSizeY = None
    else:
        # GRID
        desired_buckets = max(1, target // max(1, max_per_cell))
        g = max(1, math.sqrt(desired_buckets))
        cellSizeX = rangeX / g
        cellSizeY = rangeY / g
        buckets = {}
        for n in nodes:
            ix = int((n["x"] - minX) / cellSizeX)
            iy = int((n["y"] - minY) / cellSizeY)
            key = (ix, iy)
            b = buckets.get(key)
            if b is None:
                buckets[key] = [n]
            elif len(b) < max_per_cell:
                b.append(n)
        selected_nodes = [n for b in buckets.values() for n in b]
        if len(selected_nodes) > target:
            selected_nodes = selected_nodes[:target]

    if shuffle_nodes:
        random.shuffle(selected_nodes)

    served_nodes = len(selected_nodes)
    selected_ids = {n["id"] for n in selected_nodes}

    # --- Edge Sampling ---
    served_edges = []
    total_edges_file = 0
    if include_edges and served_nodes:
        # Determine edge target
        if max_edges is not None:
            edge_cap = None if max_edges < 0 else max_edges
        else:
            # edge_factor path
            edge_cap = int(served_nodes * edge_factor)

        # Per-node degree limiting
        unlimited_degree = (degree_cap <= 0)
        per_degree = {} if unlimited_degree else {nid: 0 for nid in selected_ids}

        if edge_mode == "random":
            candidates = []
            with open(EDGES_CSV, newline="", encoding="utf-8") as f:
                r = csv.DictReader(f)
                for row in r:
                    total_edges_file += 1
                    s = row.get("source"); t = row.get("target")
                    if s in selected_ids and t in selected_ids:
                        candidates.append((s, t))
            random.shuffle(candidates)
            for s, t in candidates:
                if edge_cap is not None and len(served_edges) >= edge_cap:
                    break
                if unlimited_degree or (per_degree[s] < degree_cap and per_degree[t] < degree_cap):
                    served_edges.append({"source": s, "target": t})
                    if not unlimited_degree:
                        per_degree[s] += 1
                        per_degree[t] += 1
        else:
            with open(EDGES_CSV, newline="", encoding="utf-8") as f:
                r = csv.DictReader(f)
                for row in f:
                    row = row if isinstance(row, dict) else {}
                f.seek(0)
                r = csv.DictReader(f)
                for row in r:
                    total_edges_file += 1
                    if edge_cap is not None and len(served_edges) >= edge_cap:
                        break
                    s = row.get("source"); t = row.get("target")
                    if s in selected_ids and t in selected_ids:
                        if unlimited_degree or (per_degree[s] < degree_cap and per_degree[t] < degree_cap):
                            served_edges.append({"source": s, "target": t})
                            if not unlimited_degree:
                                per_degree[s] += 1
                                per_degree[t] += 1
    else:
        include_edges = False

    meta = {
        "total_nodes": total_nodes,
        "served_nodes": served_nodes,
        "sampling_mode": ("none" if select_all else sampling_mode),
        "target_param": target_param,
        "max_per_cell": max_per_cell,
        "bucket_count": len(buckets) if buckets else None,
        "cell_size": [cellSizeX, cellSizeY] if cellSizeX is not None else None,
        "edge_mode": edge_mode,
        "edge_factor": edge_factor,
        "max_edges_param": max_edges if max_edges is not None else None,
        "degree_cap": (None if degree_cap <= 0 else degree_cap),
        "served_edges": len(served_edges),
        "total_edges_file": total_edges_file if include_edges else None,
        "shuffle_nodes": shuffle_nodes
    }

    result = {
        "nodes": selected_nodes,
        "links": served_edges,
        "meta": meta
    }

    return app.response_class(
        response=json.dumps(result),
        status=200,
        mimetype="application/json"
    )

if __name__ == "__main__":
    app.run(host=BACKEND_HOST, port=BACKEND_PORT, debug=(os.getenv("FLASK_ENV") == "development"))
