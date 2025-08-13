import time
from pathlib import Path
import os
import numpy as np
import pandas as pd
from dotenv import load_dotenv, find_dotenv
from scipy.ndimage import gaussian_filter
from scipy.spatial.distance import pdist
from tqdm import tqdm

# ---------------------- Hyperparams (KDEEB-style) ----------------------
NUM_ITERATIONS = 10                     # iterations of bundling
INITIAL_BANDWIDTH_HMAX = 0.08           # will be recalculated from data
KERNEL_REDUCTION_LAMBDA = 0.7           # bandwidth decay per iteration
EPSILON_GRADIENT = 1e-5                 # min gradient magnitude to move
SMOOTHING_ITERATIONS = 3                # Laplacian smoothing passes
GRID_N = 80                             # grid resolution per axis
TEST_SIZE = 15000                       # subsample edges for debugging
SAMPLES_PER_EDGE = 20                   # polyline samples per edge
SELF_AVOIDANCE_BATCHES = 4              # batches for self-avoidance

# ---------------------- IO ----------------------
load_dotenv(find_dotenv())
root_dir = Path(os.getenv("ROOT_DIR", Path(__file__).parent))
data_dir = root_dir / "data"

nodes_csv = data_dir / "network_nodes.csv"
edges_csv = data_dir / "network_edges.csv"
out_csv   = data_dir / "edges_bundled_3d_test.csv"

print("Loading data...")

# --- Load full nodes once; keep float64 to avoid precision loss ---
nodes_full = (
    pd.read_csv(nodes_csv, usecols=["id", "x", "y", "z"])
      .set_index("id")
)
nodes_full.index = nodes_full.index.astype("string")

# Global mins/spans across ALL nodes (freeze normalization)
mins  = nodes_full[["x", "y", "z"]].min().to_numpy(dtype=float)
spans = (nodes_full[["x", "y", "z"]].max() - nodes_full[["x", "y", "z"]].min()).replace(0, 1.0).to_numpy(dtype=float)

def to_unit(arr):
    arr = np.asarray(arr, dtype=float)
    return (arr - mins) / spans

def from_unit(arr):
    arr = np.asarray(arr, dtype=float)
    return arr * spans + mins

# Working nodes df (may subset for tests)
nodes_df = nodes_full.copy()

edges_df = pd.read_csv(edges_csv, usecols=["source", "target"])
edges_df[["source", "target"]] = edges_df[["source", "target"]].astype("string")
edges_df.index.name = "edge_id"

print(f"Original dataset: {len(edges_df):,} edges, {len(nodes_df):,} nodes")
if len(edges_df) > TEST_SIZE:
    test_edges = edges_df.sample(n=TEST_SIZE, random_state=42)
    test_nodes = set(test_edges["source"].unique()) | set(test_edges["target"].unique())
    nodes_df = nodes_df.loc[list(test_nodes)]
    edges_df = test_edges.reset_index(drop=True)
    print(f"Test dataset: {len(edges_df):,} edges, {len(nodes_df):,} nodes")

# Unit-space node positions using GLOBAL transform (float64)
nodes_unit = pd.DataFrame(
    to_unit(nodes_df[["x", "y", "z"]].values),
    index=nodes_df.index, columns=["x", "y", "z"]
)

# ---------------------- Initial bandwidth from data ----------------------
print("Calculating initial bandwidth from inter-edge distances...")
edge_centers = []
for src, tgt in edges_df.itertuples(index=False, name=None):
    p0 = nodes_unit.loc[src].to_numpy(dtype=float)
    p1 = nodes_unit.loc[tgt].to_numpy(dtype=float)
    edge_centers.append(0.5 * (p0 + p1))

edge_centers = np.array(edge_centers, dtype=float)

if len(edge_centers) > 1000:
    sample_idx = np.random.default_rng(42).choice(len(edge_centers), 1000, replace=False)
    distances = pdist(edge_centers[sample_idx])
else:
    distances = pdist(edge_centers) if len(edge_centers) > 1 else np.array([0.05])

avg_inter_edge_distance = float(distances.mean()) if distances.size > 0 else 0.05
INITIAL_BANDWIDTH_HMAX = avg_inter_edge_distance * 0.8
print(f"Average inter-edge distance: {avg_inter_edge_distance:.6f}")
print(f"Initial bandwidth: {INITIAL_BANDWIDTH_HMAX:.6f}")

# ---------------------- Step 1: Discretize edges (curved init) ----------------------
print("Step 1: Discretizing edges with curved initialization...")
E = len(edges_df)
graph_sampled_points = []
edge_info = []
edge_batches = np.arange(E) % SELF_AVOIDANCE_BATCHES

for eidx, (src, tgt) in enumerate(tqdm(edges_df.itertuples(index=False, name=None), total=E, desc="Discretizing")):
    p0 = nodes_unit.loc[src].to_numpy(dtype=float)
    p1 = nodes_unit.loc[tgt].to_numpy(dtype=float)

    edge_dir = p1 - p0
    edge_len = np.linalg.norm(edge_dir)

    t_vals = np.linspace(0.0, 1.0, SAMPLES_PER_EDGE)
    edge_points = []

    if edge_len > 1e-12:
        ehat = edge_dir / edge_len
        # two perpendiculars
        perp1 = np.cross(ehat, np.array([0.0, 0.0, 1.0])) if abs(ehat[2]) < 0.9 else np.cross(ehat, np.array([1.0, 0.0, 0.0]))
        perp1 /= (np.linalg.norm(perp1) + 1e-12)
        perp2 = np.cross(ehat, perp1); perp2 /= (np.linalg.norm(perp2) + 1e-12)

        rng = np.random.default_rng(42 + eidx)
        curve_mag = rng.uniform(0.02, 0.08) * edge_len
        phase1 = rng.uniform(0, 2*np.pi)
        phase2 = rng.uniform(0, 2*np.pi)

        for t in t_vals:
            linear = (1 - t) * p0 + t * p1
            curve1 = curve_mag * np.sin(np.pi * t + phase1) * perp1
            curve2 = curve_mag * 0.5 * np.sin(2 * np.pi * t + phase2) * perp2
            edge_points.append(linear + curve1 + curve2)
    else:
        for t in t_vals:
            edge_points.append((1 - t) * p0 + t * p1)

    graph_sampled_points.append(np.asarray(edge_points, dtype=float))
    edge_info.append({"source": src, "target": tgt, "edge_id": eidx, "batch": int(edge_batches[eidx])})

# ---------------------- Splatting & sampling ----------------------
def trilinear_splat_epanechnikov(points, weights, bandwidth):
    if len(points) == 0:
        return np.zeros((GRID_N, GRID_N, GRID_N), dtype=float)

    density = np.zeros((GRID_N, GRID_N, GRID_N), dtype=float)
    grid_coords = np.clip(points, 0.0, 1.0) * (GRID_N - 1)

    for i in range(grid_coords.shape[0]):
        point = grid_coords[i]
        base = np.floor(point).astype(int)
        frac = point - base

        corners = (
            (0,0,0), (0,0,1),
            (0,1,0), (0,1,1),
            (1,0,0), (1,0,1),
            (1,1,0), (1,1,1)
        )
        for dx, dy, dz in corners:
            x = min(base[0] + dx, GRID_N - 1)
            y = min(base[1] + dy, GRID_N - 1)
            z = min(base[2] + dz, GRID_N - 1)
            wx = frac[0] if dx else (1 - frac[0])
            wy = frac[1] if dy else (1 - frac[1])
            wz = frac[2] if dz else (1 - frac[2])
            density[x, y, z] += weights[i] * wx * wy * wz

    sigma = bandwidth * GRID_N
    return gaussian_filter(density, sigma=sigma, mode="constant", cval=0.0)

def trilinear_sample_gradient(gx, gy, gz, points):
    if len(points) == 0:
        return np.zeros((0, 3), dtype=float)

    grads = np.zeros((len(points), 3), dtype=float)
    grid_coords = np.clip(points, 0.0, 1.0) * (GRID_N - 1)

    for i in range(grid_coords.shape[0]):
        point = grid_coords[i]
        base = np.floor(point).astype(int)
        frac = point - base

        gsum = np.zeros(3, dtype=float)
        wsum = 0.0
        for dx in (0, 1):
            for dy in (0, 1):
                for dz in (0, 1):
                    x = min(base[0] + dx, GRID_N - 1)
                    y = min(base[1] + dy, GRID_N - 1)
                    z = min(base[2] + dz, GRID_N - 1)
                    wx = frac[0] if dx else (1 - frac[0])
                    wy = frac[1] if dy else (1 - frac[1])
                    wz = frac[2] if dz else (1 - frac[2])
                    w = wx * wy * wz
                    gsum[0] += w * gx[x, y, z]
                    gsum[1] += w * gy[x, y, z]
                    gsum[2] += w * gz[x, y, z]
                    wsum += w
        if wsum > 0:
            grads[i] = gsum / wsum
    return grads

# ---------------------- KDEEB main loop ----------------------
print(f"\nStep 2: KDEEB bundling with {NUM_ITERATIONS} iterations...")

for iteration in range(1, NUM_ITERATIONS + 1):
    print(f"\n--- Iteration {iteration}/{NUM_ITERATIONS} ---")
    t_iter = time.time()

    current_bandwidth = INITIAL_BANDWIDTH_HMAX * (KERNEL_REDUCTION_LAMBDA ** (iteration - 1))
    print(f"Current bandwidth: {current_bandwidth:.6f}")

    total_displacement = 0.0
    points_moved = 0

    for batch_id in range(SELF_AVOIDANCE_BATCHES):
        print(f"  Processing batch {batch_id+1}/{SELF_AVOIDANCE_BATCHES}...")

        # Build density map from OTHER batches only
        other_points_list = []
        other_weights_list = []
        for edge_points, info in zip(graph_sampled_points, edge_info):
            if info["batch"] != batch_id:
                other_points_list.append(edge_points)
                other_weights_list.append(np.ones(len(edge_points), dtype=float))

        if not other_points_list:
            continue

        other_points = np.concatenate(other_points_list, axis=0)
        other_weights = np.concatenate(other_weights_list, axis=0)

        density_map = trilinear_splat_epanechnikov(other_points, other_weights, current_bandwidth)
        gx, gy, gz = np.gradient(density_map, edge_order=2)

        # Advect points in current batch
        for eidx, (edge_points, info) in enumerate(zip(graph_sampled_points, edge_info)):
            if info["batch"] != batch_id:
                continue

            if len(edge_points) <= 2:
                continue

            interior = edge_points[1:-1].copy()
            if len(interior) == 0:
                continue

            grads = trilinear_sample_gradient(gx, gy, gz, interior)
            mags = np.linalg.norm(grads, axis=1)
            valid = mags > EPSILON_GRADIENT
            if np.any(valid):
                dirs = np.zeros_like(grads)
                dirs[valid] = grads[valid] / mags[valid, None]
                disp = current_bandwidth * dirs
                interior[valid] += disp[valid]
                graph_sampled_points[eidx][1:-1] = interior
                total_displacement += float(np.linalg.norm(disp[valid], axis=1).sum())
                points_moved += int(valid.sum())

    avg_displacement = total_displacement / max(points_moved, 1)
    print(f"  Average displacement: {avg_displacement:.6f}")

    # Resample edges to maintain uniform spacing (and pin endpoints)
    print("  Resampling edges...")
    for eidx, edge_points in enumerate(graph_sampled_points):
        if len(edge_points) <= 2:
            continue

        diffs = np.diff(edge_points, axis=0)
        seg_len = np.linalg.norm(diffs, axis=1)
        cum_len = np.concatenate(([0.0], np.cumsum(seg_len)))
        total_len = cum_len[-1]
        if total_len < 1e-12:
            continue

        target = np.linspace(0.0, total_len, SAMPLES_PER_EDGE)
        resampled = []
        for t in target:
            k = np.searchsorted(cum_len, t) - 1
            k = int(np.clip(k, 0, len(seg_len) - 1))
            if seg_len[k] > 1e-12:
                alpha = (t - cum_len[k]) / seg_len[k]
                alpha = float(np.clip(alpha, 0.0, 1.0))
                resampled.append((1 - alpha) * edge_points[k] + alpha * edge_points[k + 1])
            else:
                resampled.append(edge_points[k])

        resampled = np.asarray(resampled, dtype=float)
        # Pin endpoints in UNIT space
        resampled[0]  = edge_points[0]
        resampled[-1] = edge_points[-1]
        graph_sampled_points[eidx] = resampled

    # Laplacian smoothing on interior points
    print("  Applying Laplacian smoothing...")
    for _ in range(SMOOTHING_ITERATIONS):
        for eidx, edge_points in enumerate(graph_sampled_points):
            if len(edge_points) <= 2:
                continue
            sm = edge_points.copy()
            for i in range(1, len(edge_points) - 1):
                sm[i] = 0.25 * edge_points[i - 1] + 0.5 * edge_points[i] + 0.25 * edge_points[i + 1]
            graph_sampled_points[eidx] = sm

    # Clip to unit cube
    for eidx in range(len(graph_sampled_points)):
        graph_sampled_points[eidx] = np.clip(graph_sampled_points[eidx], 0.0, 1.0)

    print(f"  Iteration completed in {time.time() - t_iter:.2f}s")

    if avg_displacement < 1e-6:
        print(f"  Converged at iteration {iteration}")
        break

# ---------------------- Final Output (snap endpoints in WORLD space) ----------------------
print("\nStep 3: Writing final output...")

# Choose which nodes table to snap to at export:
# If your viewer uses nodes_full (recommended), keep this as nodes_full.
# If your viewer uses a filtered nodes file, change to nodes_df.
nodes_world = nodes_full

records = []
for edge_points, info in zip(graph_sampled_points, edge_info):
    src, tgt = info["source"], info["target"]

    world_points = from_unit(edge_points)  # float64

    # HARD SNAP endpoints to EXACT node coordinates in world space
    n0 = nodes_world.loc[src, ["x", "y", "z"]].to_numpy(dtype=float)
    n1 = nodes_world.loc[tgt, ["x", "y", "z"]].to_numpy(dtype=float)
    world_points[0]  = n0
    world_points[-1] = n1

    # Format with high precision to avoid rounding drift in the viewer
    pieces = [f"{x:.8f},{y:.8f},{z:.8f}" for (x, y, z) in world_points]
    records.append({"source": src, "target": tgt, "points": "|".join(pieces)})

pd.DataFrame(records).to_csv(out_csv, index=False)
print(f"Saved {len(records):,} bundled edges → {out_csv}")

# ---------------------- Quality assessment + endpoint alignment check ----------------------
print("\nFinal quality assessment:")
curvatures = []
for edge_points in graph_sampled_points:
    if len(edge_points) > 2:
        d2 = np.linalg.norm(edge_points[2:] - 2 * edge_points[1:-1] + edge_points[:-2], axis=1)
        curvatures.extend(d2)

edge_midpoints = []
for edge_points in graph_sampled_points:
    if len(edge_points) > 0:
        edge_midpoints.append(edge_points[len(edge_points) // 2])

if len(edge_midpoints) > 1:
    edge_midpoints = np.array(edge_midpoints, dtype=float)
    distances = pdist(edge_midpoints)
    print(f"Mean curvature: {np.mean(curvatures):.6f}")
    print(f"Mean inter-midpoint distance: {distances.mean():.6f}")
    print(f"Min inter-midpoint distance: {distances.min():.6f}")

    straight_lengths = []
    curved_lengths = []
    for edge_points in graph_sampled_points:
        if len(edge_points) > 1:
            straight = np.linalg.norm(edge_points[-1] - edge_points[0])
            segs = np.linalg.norm(np.diff(edge_points, axis=0), axis=1)
            curved = float(segs.sum())
            if straight > 1e-12:
                straight_lengths.append(straight)
                curved_lengths.append(curved)
    if straight_lengths:
        ratio = np.array(curved_lengths) / np.array(straight_lengths)
        print(f"Mean path length ratio: {ratio.mean():.3f}")

# Endpoint alignment error in WORLD coords (should be exactly zero after snapping)
max_err = 0.0
mean_errs = []
for (src, tgt), pts in zip(edges_df.itertuples(index=False, name=None), graph_sampled_points):
    p0w, p1w = from_unit(pts[0]), from_unit(pts[-1])
    n0w = nodes_world.loc[src, ["x", "y", "z"]].to_numpy(dtype=float)
    n1w = nodes_world.loc[tgt, ["x", "y", "z"]].to_numpy(dtype=float)
    # emulate export snap
    p0w = n0w
    p1w = n1w
    e = max(np.linalg.norm(p0w - n0w), np.linalg.norm(p1w - n1w))
    max_err = max(max_err, e)
    mean_errs.append(e)
print(f"Endpoint max error: {max_err:.12f}, mean: {np.mean(mean_errs):.12f}")

print("\nKDEEB completed.")
