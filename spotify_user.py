from typing import Set, List
import asyncio

from ..api.spotify_api import SpotifyClient


async def fetch_user_artist_ids(
    client: SpotifyClient,
    page_limit: int = 50,
) -> Set[str]:
    """
    Walk through the user's 'Liked Songs' and return a deduplicated set
    of all artist IDs that appear on those tracks.

    Parameters
    ----------
    client : SpotifyClient
        Your authenticated API wrapper.
    page_limit : int, optional
        Number of tracks to ask for per page (1 to 50). 50 is maximal and fastest.

    Returns
    -------
    Set[str]
        All distinct Spotify artist IDs found in the library.
    """
    assert 1 <= page_limit <= 50

    url = "https://api.spotify.com/v1/me/tracks"
    artist_ids: Set[str] = set()

    params = {"limit": page_limit, "offset": 0}

    while True:
        payload = await client.get(url, params=params)  # GET /v1/me/tracks
        for item in payload["items"]:
            track = item["track"]
            for artist in track["artists"]:
                artist_ids.add(artist["id"])

        if payload["next"] is None:
            break

        params["offset"] += page_limit

    return artist_ids


async def seed_graph_from_user_library(client: SpotifyClient) -> None:
    """
    End-to-end example:
    1. Pull every artist from liked songs
    2. Hand them to the 'build_network' helper you already have
       (assumed to live in spotify_apy.py).

    This keeps spotify_user.py strictly responsible for the *who*,
    leaving spotify_apy.py in charge of building the *graph*.
    """
    from spotify_apy import build_similarity_graph  # import lazily to avoid cycles

    user_artists = await fetch_user_artist_ids(client)
    await build_similarity_graph(client, artist_ids=list(user_artists))


# quick script entry-point -----------------------------------------------------

if __name__ == "__main__":
    import os

    async def _main():
        token = os.environ["SPOTIFY_TOKEN"]  # must include user-library-read scope
        client = SpotifyClient(token)
        artists = await fetch_user_artist_ids(client)
        print(f"Collected {len(artists)} unique artists")

    asyncio.run(_main())
