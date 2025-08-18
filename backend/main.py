import os
import csv
from pathlib import Path
from dotenv import load_dotenv
from flask import Flask, request, jsonify, redirect, session, send_from_directory
from flask_cors import CORS
from spotipy.oauth2 import SpotifyOAuth
from flask_compress import Compress
import spotipy


env_path = Path(__file__).parent / ".env"
if env_path.exists():
    load_dotenv(env_path)

BACKEND_HOST = os.getenv("BACKEND_HOST")
BACKEND_PORT = int(os.getenv("BACKEND_PORT"))
FRONTEND_ORIGIN = (os.getenv("FRONTEND_ORIGIN", ""))
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
SPOTIFY_REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI")

STATIC_DIR = Path(__file__).parent.parent / "frontend" / ("dist" if (FRONTEND_ORIGIN == "" and (Path(__file__).parent.parent / "frontend" / "dist").exists())else "public")

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="")
app.secret_key = os.getenv("APP_SECRET")

app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = False
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_PERMANENT'] = False
Compress(app)

cors_origins = [FRONTEND_ORIGIN]
cors_resources = {
    r"/auth/*": {"origins": cors_origins or ["http://localhost:5173"]},
    r"/data/*": {"origins": "*"},
    r"/api/*": {"origins": cors_origins or ["http://localhost:5173"]}
}

CORS(app, resources=cors_resources, methods=["GET", "POST"], allow_headers="*", supports_credentials=True)

sp_oauth = SpotifyOAuth(
    client_id=SPOTIFY_CLIENT_ID,
    client_secret=SPOTIFY_CLIENT_SECRET,
    redirect_uri=SPOTIFY_REDIRECT_URI,
    scope="user-library-read playlist-read-private playlist-read-collaborative"
)

@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")

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
    
    try:
        print("Getting token")
        token_info = sp_oauth.get_access_token(code, as_dict=True)
        session["spotify_token_info"] = token_info
        session.permanent = True        
        return redirect(frontend_url("/network.html"))
    except Exception as e:
        print(f"Error getting access token: {e}")
        return jsonify(error="Failed to get access token"), 400

@app.route("/api/me")
def api_me():
    """
    Check if user is authenticated by verifying session token.
    """
    token_info = session.get("spotify_token_info", None)
    if not token_info:
        return jsonify(error="Not authenticated"), 401
    
    # Check if token is expired and refresh if needed
    try:
        if sp_oauth.is_token_expired(token_info):
            if 'refresh_token' in token_info:
                token_info = sp_oauth.refresh_access_token(token_info['refresh_token'])
                session["spotify_token_info"] = token_info
            else:
                return jsonify(error="Token expired and no refresh token available"), 401
    except Exception as e:
        print(f"Error refreshing token: {e}")
        return jsonify(error="Token refresh failed"), 401
    
    return jsonify(status="ok", authenticated=True)

@app.route("/auth/logout")
def logout():
    """
    Clear the user's session and redirect to frontend.
    """
    session.clear()
    return redirect(frontend_url("/network.html"))


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


def get_spotify_client():
    """
    Returns authenticated Spotipy client.
    """
    token_info = session.get("spotify_token_info", None)
    if not token_info:
        return None

    # Check if token is expired and refresh if needd
    try:
        if sp_oauth.is_token_expired(token_info):
            if 'refresh_token' in token_info:
                token_info = sp_oauth.refresh_access_token(token_info['refresh_token'])
                session["spotify_token_info"] = token_info
                print("Token refreshed successfully")
            else:
                print("Token expired and no refresh token available")
                return None
    except Exception as e:
        print(f"Error refreshing token: {e}")
        return None

    return spotipy.Spotify(auth=token_info['access_token'])


@app.route("/api/playlists")
def get_user_playlists():
    """
    Fetch the current user's playlists.
    """
    print("Fetching user playlists")
    sp = get_spotify_client()
    if not sp:
        print("User not authenticated")
        return jsonify(error="User not authenticated"), 401

    try:
        print("Fetching Playlists")
        results = sp.current_user_playlists(limit=50)
        playlists = []
        for item in results['items']:
            playlists.append({
                "id": item['id'],
                "name": item['name'],
                "total_tracks": item['tracks']['total']
            })
        print(f"Playlist fetch success")
        return jsonify(playlists)
    except spotipy.exceptions.SpotifyException as e:
        print(f"Spotify API error: {e}")
        if e.http_status == 401:
            return jsonify(error="Token invalid or expired"), 401
        return jsonify(error=f"Spotify API error: {str(e)}"), 500
    except Exception as e:
        print(f"Unexpected error: {e}")
        return jsonify(error=str(e)), 500
    

@app.route("/api/playlist/<string:playlist_id>/artists")
def get_playlist_artists(playlist_id):
    """
    Fetch all unique artists from a specific playlist ID.
    """
    print(f"Fetching unique artists for playlist: {playlist_id}")
    sp = get_spotify_client()
    if not sp:
        print("User not authenticated")
        return jsonify(error="User not authenticated"), 401

    try:
        unique_artists = []
        seen_artist_ids = set() # Use a set for efficient duplicate checking

        # Fetch the playlist items (tracks) page by page
        results = sp.playlist_items(playlist_id)
        
        while results:
            for item in results['items']:
                track = item.get('track')
                # A track might not have artists, so we check
                if track and track.get('artists'):
                    # Iterate through each artist credited on the track
                    for artist in track['artists']:
                        # If we haven't seen this artist ID before, add them
                        if artist['id'] and artist['id'] not in seen_artist_ids:
                            seen_artist_ids.add(artist['id'])
                            unique_artists.append({
                                'id': artist['id'],
                                'name': artist['name']
                            })
            
            # Get the next page of tracks, if it exists
            if results['next']:
                results = sp.next(results)
            else:
                results = None # Exit the loop

        print(f"Successfully fetched {len(unique_artists)} unique artists for playlist {playlist_id}")
        return jsonify(unique_artists)

    except spotipy.exceptions.SpotifyException as e:
        print(f"Spotify API error for playlist {playlist_id}: {e}")
        if e.http_status == 404:
            return jsonify(error="Playlist not found"), 404
        if e.http_status == 401:
            return jsonify(error="Token invalid or expired"), 401
        return jsonify(error=f"Spotify API error: {str(e)}"), 500
    except Exception as e:
        print(f"Unexpected error for playlist {playlist_id}: {e}")
        return jsonify(error=str(e)), 500
    
if __name__ == "__main__":
    app.run(host=BACKEND_HOST, port=BACKEND_PORT, debug=(os.getenv("FLASK_ENV") == "development"))
