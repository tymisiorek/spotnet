from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from backend.api.spotify_api import SpotifyClient

oauth_scheme = HTTPBearer(auto_error=False)
graph_router = APIRouter(tags=["graph"])

@graph_router.get("/graph")
async def get_graph(
    creds: HTTPAuthorizationCredentials | None = Depends(oauth_scheme),
):
    if creds is None:
        raise HTTPException(status_code=401, detail="Bearer token required")

    access_token = creds.credentials
    async with SpotifyClient(access_token) as sp:
        nodes, links = await sp.build_graph()

    if not nodes:
        raise HTTPException(status_code=404, detail="graph empty")

    return {"nodes": nodes, "links": links}
