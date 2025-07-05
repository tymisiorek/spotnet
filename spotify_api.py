import aiohttp
from collections import defaultdict
from typing import Any, Dict, List, Tuple

BASE_URL = "https://api.spotify.com/v1"

class SpotifyClient:
    """
    Thin async wrapper around the Spotify Web-API that builds a simple
    artist-collaboration graph from the user's saved tracks.
    """

    def __init__(self, token: str) -> None:
        self.token = token
        self.session: aiohttp.ClientSession | None = None

    # ── async context manager ────────────────────────────────────────────────
    async def __aenter__(self) -> "SpotifyClient":
        self.session = aiohttp.ClientSession(
            headers={"Authorization": f"Bearer {self.token}"}
        )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self.session:
            await self.session.close()

    # ── helpers ──────────────────────────────────────────────────────────────
    async def _get(self, url: str, params: Dict[str, Any] | None = None) -> Dict[str, Any]:
        async with self.session.get(url, params=params) as resp:           # type: ignore[attr-defined]
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"Spotify API {resp.status}: {text}")
            return await resp.json()

    async def _paginate(self, endpoint: str, key: str) -> List[Dict[str, Any]]:
        """
        Generic paginator for endpoints that return 'items' and a 'next' URL.
        """
        results: List[Dict[str, Any]] = []
        url     = f"{BASE_URL}{endpoint}"
        params  = {"limit": 50, "offset": 0}

        while url:
            data   = await self._get(url, params=params)
            results.extend(data[key])
            url    = data.get("next")
            params = None   # Spotify 'next' already has the query string
        return results

    # ── public API ───────────────────────────────────────────────────────────
    async def build_graph(self) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Build an artist-collaboration graph from the user's saved tracks.
        Nodes: distinct artists
        Links: undirected edges between every pair of artists on the same track
        """
        saved_tracks = await self._paginate("/me/tracks", key="items")

        # collect artist info and co-appearance counts
        artist_info: Dict[str, str] = {}                # id -> name
        edges: defaultdict[tuple[str, str], int] = defaultdict(int)

        for item in saved_tracks:
            track = item["track"]
            artists = track.get("artists", [])
            ids = [a["id"] for a in artists if a["id"]]
            names = {a["id"]: a["name"] for a in artists}
            artist_info.update(names)

            # add co-appearance edges (combinatorial pairs)
            for i in range(len(ids)):
                for j in range(i + 1, len(ids)):
                    edge = tuple(sorted((ids[i], ids[j])))
                    edges[edge] += 1

        # format for ForceGraph
        nodes = [{"id": aid, "label": artist_info[aid]} for aid in artist_info]
        links = [{"source": s, "target": t, "value": w} for (s, t), w in edges.items()]

        return nodes, links
