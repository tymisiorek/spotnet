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
from flask_compress import Compress


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
Compress(app)

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


@app.route("/data/graph")
def data_graph():
    graph_path = DATA_DIR / "graph.json"
    if not graph_path.exists():
        return jsonify(error="graph file not found"), 404
    with open(graph_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Fallbacks for missing fields
    nodes = data.get("nodes", [])
    for n in nodes:
        if "community" not in n or n["community"] is None:
            n["community"] = 0
        if "degree" not in n or n["degree"] is None:
            n["degree"] = 0
        if "z" not in n or n["z"] is None:
            n["z"] = 0.0

    return jsonify(data)

if __name__ == "__main__":
    app.run(host=BACKEND_HOST, port=BACKEND_PORT, debug=(os.getenv("FLASK_ENV") == "development"))
