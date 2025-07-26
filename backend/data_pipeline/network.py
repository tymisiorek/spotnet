import math, os
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import load_dotenv, find_dotenv
from tqdm import tqdm
from datashader.bundling import hammer_bundle

load_dotenv(find_dotenv())

root = Path(os.getenv("ROOT", Path(__file__).parent))
data = f"{root}data"

nodes_csv = f"{data}network_nodes.csv"
edges_csv = f"{data}network_edges.csv"
out_csv = f"{data}edges_bundled.csv"

BATCH_SIZE = 20000 # hammer_bundle batch size
THRESH_FRAC = 0.15 # xy distance fraction that defines ramp start
RAMP_EXP = 3 
SHARED_HEIGHT = "midpoint"

nodes_df = (pd.read_csv(nodes_csv, usecols=["id", "x", "y", "z"]).set_index("id"))
nodes_df.index = nodes_df.index.astype("string")

edges_df = pd.read_csv(edges_csv, usecols=["source", "target"])
edges_df[["source", "target"]] = edges_df[["source", "target"]].astype("string")
edges_df.index.name = "edge_id"

#bundle (doesnt work on z)
bundler = hammer_bundle.instance(
    x="x", y="y",
    source="source", target="target",
    include_edge_id=True,
    batch_size=BATCH_SIZE
)

paths_df = bundler(nodes_df[["x", "y"]], edges_df)

records = []
edge_groups = paths_df.groupby("edge_id")

#attempt to preserve bundles and curvatures while adding z axis back into graph (really doesnt work)
for eid, path in tqdm(edge_groups, desc="post processing", unit="edge"):
    src = edges_df.loc[eid, "source"]
    tgt = edges_df.loc[eid, "target"]

    x0, y0, z0 = nodes_df.loc[src, ["x", "y", "z"]]
    x1, y1, z1 = nodes_df.loc[tgt, ["x", "y", "z"]]

    if SHARED_HEIGHT == "median":
        z_mid = np.median([z0, z1])
    else: # midpoint
        z_mid = 0.5 * (z0 + z1)

    xy = path[["x", "y"]].dropna().values
    n  = len(xy)
    if n < 2:
        continue

    # Get ramp boundaries
    d_total = math.hypot(x1 - x0, y1 - y0)
    xy_thresh = THRESH_FRAC * d_total

    # distance to source / target for each sample
    d_src = np.hypot(xy[:, 0] - x0, xy[:, 1] - y0)
    d_tgt = np.hypot(xy[:, 0] - x1, xy[:, 1] - y1)

    # index where path first leaves source proximity
    idx_src_ramp_end = np.argmax(d_src > xy_thresh)
    if d_src[0] > xy_thresh: # edge already far
        idx_src_ramp_end = 0

    # index where path last is close to target
    idx_tgt_ramp_start = n - 1 - np.argmax(d_tgt[::-1] > xy_thresh)
    if d_tgt[-1] > xy_thresh:
        idx_tgt_ramp_start = n - 1

    t_src_end = idx_src_ramp_end / (n - 1)
    t_tgt_start = idx_tgt_ramp_start / (n - 1)

    # create z coords
    pts_out = []
    for i, (x, y) in enumerate(xy):
        t = i / (n - 1)             # 0-1
        if t < t_src_end:           # source
            if t_src_end == 0:
                z = z_mid 
            else:
                u = t / t_src_end
                z = z0 + (z_mid - z0) * u**RAMP_EXP
        elif t > t_tgt_start:       # target
            if t_tgt_start == 1:
                z = z_mid
            else:
                u = (t - t_tgt_start) / (1 - t_tgt_start)
                z = z_mid + (z1 - z_mid) * u**RAMP_EXP
        else:
            z = z_mid

        pts_out.append(f"{x:.6f},{y:.6f},{z:.6f}")

    records.append({"source": src, "target": tgt, "points": "|".join(pts_out)})

bundled_df = pd.DataFrame(records)
bundled_df.to_csv(out_csv, index=False)
print("wrote bundled edges to csv")
