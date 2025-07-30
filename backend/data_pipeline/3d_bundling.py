import time
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import load_dotenv, find_dotenv
from scipy.ndimage import gaussian_filter
from tqdm import tqdm
import os

# hyperparams
GRID_N = 128 # voxel resolution per axis
SIGMA = 2.7 # Gaussian blur radius, in voxels
ITERATIONS = 22 # KDE/advection passes
STEP_FACTOR = 0.8 # fraction of a voxel moved per iteration
SAMPLES_PER_EDGE = 20 # how many samples along each edge (≥ 10)


load_dotenv(find_dotenv())
root_dir  = Path(os.getenv("ROOT_DIR", Path(__file__).parent))
data_dir  = root_dir / "data"

nodes_csv = Path(f"{data_dir}/network_nodes.csv")
edges_csv = Path(f"{data_dir}/network_edges.csv")
out_csv   = Path(f"{data_dir}/edges_bundled_3d.csv")


nodes_df = (pd.read_csv(nodes_csv, usecols=["id", "x", "y", "z"]).set_index("id").astype({"x": "float32", "y": "float32", "z": "float32"}))
nodes_df.index = nodes_df.index.astype("string")

edges_df = pd.read_csv(edges_csv, usecols=["source", "target"])
edges_df[["source", "target"]] = edges_df[["source", "target"]].astype("string")
edges_df.index.name = "edge_id"


mins = nodes_df[["x", "y", "z"]].min()
spans = nodes_df[["x", "y", "z"]].max() - mins
to_unit = lambda arr: (arr - mins.values) / spans.values
from_unit = lambda arr: arr * spans.values + mins.values

nodes_unit = to_unit(nodes_df[["x", "y", "z"]])

# Create evenly spaced samples across each edge
print("Generating initial polylines …")
pts = np.empty((len(edges_df) * SAMPLES_PER_EDGE, 3), dtype=np.float32)
segments = np.empty((len(edges_df) * SAMPLES_PER_EDGE, 2), dtype=np.int32)
w = 0

for eid, (src, tgt) in tqdm(edges_df.iterrows(), total=len(edges_df)):
    p0 = nodes_unit.loc[src].values
    p1 = nodes_unit.loc[tgt].values
    for i in range(SAMPLES_PER_EDGE):
        t = i / (SAMPLES_PER_EDGE - 1)
        pts[w] = (1 - t) * p0 + t * p1
        segments[w] = (eid, i)
        w += 1

pts = pts[:w]
segments = segments[:w]
fixed_mask = (segments[:, 1] == 0) | (segments[:, 1] == SAMPLES_PER_EDGE - 1)

#  map unit‑cube points to voxel indices
def voxelise(p):
    v = p * (GRID_N - 1)
    base = np.floor(v).astype(np.int16)
    frac = v - base
    return base, frac

# KDE Loop:
for it in range(ITERATIONS):
    tic = time.time()

    # Create a voxel density histogram
    density = np.zeros((GRID_N, GRID_N, GRID_N), dtype=np.float32)
    base, _ = voxelise(pts)
    valid = np.all((base >= 0) & (base < GRID_N), axis=1)
    vox = base[valid]
    np.add.at(density, (vox[:, 0], vox[:, 1], vox[:, 2]), 1.0)

    # Gaussian blur for approximation of kde
    density = gaussian_filter(density, SIGMA, mode="constant")

    # Gradient field
    gx, gy, gz = np.gradient(density)
    grad = np.stack([gx, gy, gz], axis=-1)

    # Interpolate gradient at each point
    moves = np.zeros_like(pts)
    base, frac = voxelise(pts)
    fx, fy, fz = frac.T

    for corner in range(8):
        cx, cy, cz = (corner & 1, (corner >> 1) & 1, (corner >> 2) & 1)
        w_corner = ((fx if cx else 1 - fx) *
                    (fy if cy else 1 - fy) *
                    (fz if cz else 1 - fz))
        ix = np.clip(base[:, 0] + cx, 0, GRID_N - 1)
        iy = np.clip(base[:, 1] + cy, 0, GRID_N - 1)
        iz = np.clip(base[:, 2] + cz, 0, GRID_N - 1)
        moves += grad[ix, iy, iz] * w_corner[:, None]

    # Move a fixed step across the gradient
    max_step = STEP_FACTOR / (GRID_N - 1)
    step_len = np.linalg.norm(moves, axis=1, keepdims=True)
    step_len[step_len == 0] = 1
    scaler = np.minimum(max_step / step_len, 1.0)
    pts[~fixed_mask] += moves[~fixed_mask] * scaler[~fixed_mask]

    print(f"iter {it + 1:>2}/{ITERATIONS}  "
          f"avg |∇ρ|={step_len.mean():.4e}  "
          f"{time.time() - tic:.2f}s")

# Write back to csv:
print("Writing bundled edges")
records, cur = [], []

for (eid, idx), (x, y, z) in zip(segments, from_unit(pts)):
    if idx == 0:
        cur = []
    cur.append(f"{x:.6f},{y:.6f},{z:.6f}")
    if idx == SAMPLES_PER_EDGE - 1:
        src, tgt = edges_df.loc[eid]
        records.append({"source": src, "target": tgt, "points": "|".join(cur)})

pd.DataFrame(records).to_csv(out_csv, index=False)
print(f"Saved {len(records):,} bundled edges → {out_csv}")
