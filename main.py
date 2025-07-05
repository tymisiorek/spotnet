import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from spotipy.oauth2 import SpotifyOAuth

from .graph_router import graph_router

# ── environment ──────────────────────────────────────────────────────────────
env_path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=env_path)

CLIENT_ID     = os.getenv("SPOTIFY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
REDIRECT_URI  = os.getenv("SPOTIFY_REDIRECT_URI")

# ── FastAPI app ───────────────────────────────────────────────────────────────
app = FastAPI(title="SpotNet API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(graph_router, prefix="/api")

@app.get("/ping")
async def ping() -> dict[str, bool]:
    return {"pong": True}

# ── Spotify OAuth ─────────────────────────────────────────────────────────────
sp_oauth = SpotifyOAuth(
    client_id=CLIENT_ID,
    client_secret=CLIENT_SECRET,
    redirect_uri=REDIRECT_URI,
    scope="user-library-read",
    cache_path=None,          # disable on-disk token caching
    show_dialog=False,
)

@app.get("/auth/login")
async def login() -> RedirectResponse:
    return RedirectResponse(sp_oauth.get_authorize_url())

@app.get("/callback")
async def callback(request: Request) -> RedirectResponse | dict[str, str]:
    code = request.query_params.get("code")
    if not code:
        return {"error": "missing code"}
    token_info = sp_oauth.get_access_token(code)
    access_token = token_info["access_token"]
    return RedirectResponse(f"http://localhost:5173/hello?token={access_token}")
