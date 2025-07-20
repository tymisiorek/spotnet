import os
from pathlib import Path
from dotenv import load_dotenv
from flask import Flask, request, jsonify, redirect
from flask_cors import CORS
from spotipy.oauth2 import SpotifyOAuth

env_path = Path(__file__).parent / ".env"
if env_path.exists():
    load_dotenv(env_path)

# --- Config values ---
BACKEND_HOST = os.getenv("BACKEND_HOST", "127.0.0.1")
BACKEND_PORT = int(os.getenv("BACKEND_PORT", "8000"))
FRONTEND_ORIGIN = (os.getenv("FRONTEND_ORIGIN", "") or "").rstrip("/")
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
SPOTIFY_REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI")

# Dist vs public decision (dev uses public, prod can swap to dist)
STATIC_DIR = Path(__file__).parent.parent / "frontend" / (
    "dist" if (FRONTEND_ORIGIN == "" and (Path(__file__).parent.parent / "frontend" / "dist").exists()) else "public"
)

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="")

cors_origins = []
if FRONTEND_ORIGIN:
    cors_origins.append(FRONTEND_ORIGIN)

# Allow dev direct access to data endpoint
cors_resources = {
    r"/auth/*": {"origins": cors_origins or ["http://localhost:5173"]},
    r"/data/*": {"origins": "*"}
}
CORS(app, resources=cors_resources, methods=["GET", "POST"], allow_headers="*")

# Spotify OAuth client
sp_oauth = SpotifyOAuth(
    client_id=SPOTIFY_CLIENT_ID,
    client_secret=SPOTIFY_CLIENT_SECRET,
    redirect_uri=SPOTIFY_REDIRECT_URI,
    scope="user-library-read"
)

def frontend_url(path: str) -> str:
    """
    Returns absolute front-end URL if FRONTEND_ORIGIN set,
    else relative path (served by same origin in prod single-domain mode).
    """
    if not path.startswith("/"):
        path = "/" + path
    if FRONTEND_ORIGIN:
        return FRONTEND_ORIGIN + path
    return path

@app.route("/ping")
def ping():
    return jsonify(pong=True)

@app.route("/auth/login")
def login():
    authorize_url = sp_oauth.get_authorize_url()
    return redirect(authorize_url)

@app.route("/callback")
def callback():
    code = request.args.get("code")
    if not code:
        return jsonify(error="no code in request"), 400

    token_info = sp_oauth.get_access_token(code)
    # Redirect to graph page
    return redirect(frontend_url("/blank.html"))

@app.route("/api/me")
def api_me():
    return jsonify(status="ok")

if __name__ == "__main__":
    app.run(host=BACKEND_HOST, port=BACKEND_PORT, debug=(os.getenv("FLASK_ENV") == "development"))
