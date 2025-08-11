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


env_path = Path(__file__).parent / ".env"
if env_path.exists():
    load_dotenv(env_path)

BACKEND_HOST = os.getenv("BACKEND_HOST")
BACKEND_PORT = int(os.getenv("BACKEND_PORT"))
FRONTEND_ORIGIN = (os.getenv("FRONTEND_ORIGIN", ""))
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
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


#paths for graph route
data  = Path(__file__).parent.parent / "data"
nodes_csv = Path(f"{data}/network_nodes.csv")
edges_csv = Path(f"{data}/edges_bundled_3d_test.csv")

def _float(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default

@app.route("/data/graph")
def data_graph():
    '''
    sends nodes and edges into json for rendering
    '''

    max_edges = request.args.get("max_edges")
    try:
        max_edges = None if max_edges in (None, "", "all") else int(max_edges)
    except ValueError:
        max_edges = None

    nodes = []
    with nodes_csv.open(newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            deg = _float(row.get("degree", 0))
            nodes.append({
                "id": row["id"],
                "name": row.get("name", ""),
                "followers": _float(row.get("followers")),
                "popularity": _float(row.get("popularity")),
                "genres": row.get("genres", ""),
                "chart_hits": row.get("chart_hits", ""),
                "top_chart": row.get("top_chart", ""),
                "x": _float(row["x"]),
                "y": _float(row["y"]),
                "z": _float(row["z"]),
                "degree": deg,
                "community": row['community']
            })

    links = []
    have_points = False
    with edges_csv.open(newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        fields = r.fieldnames or []
        have_points = "points" in fields
        count = 0
        for row in r:
            s, t = row.get("source"), row.get("target")
            if not s or not t:
                continue
            link = {"source": s, "target": t}
            if have_points:
                link["points"] = row["points"]  # keep raw string for client to parse
            links.append(link)
            count += 1
            if max_edges is not None and count >= max_edges:
                break

    return jsonify({
        "nodes": nodes,
        "links": links,
        "meta": {
            "node_count": len(nodes),
            "edge_count": len(links),
            "bundled": have_points
        }
    })
if __name__ == "__main__":
    app.run(host=BACKEND_HOST, port=BACKEND_PORT, debug=(os.getenv("FLASK_ENV") == "development"))
