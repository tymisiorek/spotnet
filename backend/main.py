import os
from pathlib import Path
from dotenv import load_dotenv

from flask import Flask, request, jsonify, redirect, url_for
from flask_cors import CORS
from spotipy.oauth2 import SpotifyOAuth

# load environment variables from .env
env_path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=env_path)

CLIENT_ID     = os.getenv("SPOTIFY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
REDIRECT_URI  = os.getenv("SPOTIFY_REDIRECT_URI")

app = Flask(
    __name__,
    static_folder=str(Path(__file__).parent.parent / "frontend" / "public"),
    static_url_path=""
)

# allow calls from your frontend
CORS(app, origins=["http://localhost:5173"], methods=["GET", "POST"], allow_headers=["*"])

@app.route("/ping")
def ping():
    return jsonify(pong=True)

# configure Spotify OAuth
sp_oauth = SpotifyOAuth(
    client_id=CLIENT_ID,
    client_secret=CLIENT_SECRET,
    redirect_uri=REDIRECT_URI,
    scope="user-library-read"
)

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
    # token_info is a dict with access_token, expires_at, etc.
    return redirect(url_for("static", filename="blank.html"))

if __name__ == "__main__":
    # adjust host/port to match your REDIRECT_URI if needed
    app.run(host="127.0.0.1", port=8000, debug=True)
