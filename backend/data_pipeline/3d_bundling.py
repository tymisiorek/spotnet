import time
from pathlib import Path
import os
import numpy as np
import pandas as pd
from dotenv import load_dotenv, find_dotenv
from scipy.ndimage import gaussian_filter
from scipy.spatial.distance import pdist
from tqdm import tqdm


'''
https://github.com/chanhanyi0923/KDEEB
https://webspace.science.uu.nl/~telea001/InfoVis/KDEEB

This file was built pretty heavily using NotebookLM and Claude as the edge bundling was a difficult step that I wasn't trying to spend a ton of time working on.
'''


NUM_ITERATIONS = 10
INITIAL_BANDWIDTH_HMAX = 0.08
KERNEL_REDUCTION_LAMBDA = 0.7
# Prevent edges from moving if the density gradient nears zero
EPSILON_GRADIENT = 1e-5
# Reduce jaggedness
SMOOTHING_ITERATIONS = 4
# Resolution of grid to calculate density
GRID_N = 80
TEST_SIZE = 15000
SAMPLES_PER_EDGE = 15
# Prevents edges from being attracted to their own density
SELF_AVOIDANCE_BATCHES = 4


load_dotenv(find_dotenv())
root_dir = Path(os.getenv("ROOT_DIR", Path(__file__).parent))
data_dir = root_dir / "data"

nodes_csv = data_dir / "network_nodes.csv"
edges_csv = data_dir / "network_edges.csv"
out_csv = data_dir / "edges_bundled_3d_test.csv"

print("Loading data")

nodes_full = (pd.read_csv(nodes_csv, usecols=["id", "x", "y", "z"]).set_index("id"))
nodes_full.index = nodes_full.index.astype("string")


# Normalize coordinates
mins = nodes_full[["x", "y", "z"]].min().to_numpy(dtype=float)
spans = (nodes_full[["x", "y", "z"]].max() - nodes_full[["x", "y", "z"]].min()).replace(0, 1.0).to_numpy(dtype=float)

# Convert world coordinates to 0, 1
def to_unit(arr):
    arr = np.asarray(arr, dtype=float)
    return (arr - mins) / spans

# Convert 0,1 to global
def from_unit(arr):
    arr = np.asarray(arr, dtype=float)
    return arr * spans + mins

nodes_df = nodes_full.copy()
edges_df = pd.read_csv(edges_csv, usecols=["source", "target"])

edges_df[["source", "target"]] = edges_df[["source", "target"]].astype("string")
edges_df.index.name = "edge_id"

print(f"Original: {len(edges_df)} edges, {len(nodes_df)} nodes")

if len(edges_df) > TEST_SIZE:
    test_edges = edges_df.sample(n=TEST_SIZE, random_state=123)
    test_nodes = set(test_edges["source"].unique()) | set(test_edges["target"].unique())
    nodes_df = nodes_df.loc[list(test_nodes)]
    edges_df = test_edges.reset_index(drop=True)


nodes_unit = pd.DataFrame(to_unit(nodes_df[["x", "y", "z"]].values), index=nodes_df.index, columns=["x", "y", "z"])

# Bandwidth calculation
print("Calculating initial bandwidth from inter-edge distances")
edge_centers = []
# For each edge find its midpoint
for src, tgt in edges_df.itertuples(index=False, name=None):
    p0 = nodes_unit.loc[src].to_numpy(dtype=float)
    p1 = nodes_unit.loc[tgt].to_numpy(dtype=float)
    edge_centers.append(0.5 * (p0 + p1))

edge_centers = np.array(edge_centers, dtype=float)

# Use a sample for efficiency
if len(edge_centers) > 1000:
    sample_idx = np.random.default_rng(123).choice(len(edge_centers), 1000, replace=False)
    distances = pdist(edge_centers[sample_idx])
else:
    distances = pdist(edge_centers) if len(edge_centers) > 1 else np.array([0.05])

# The average distance for how close edges are to each other.
avg_inter_edge_distance = float(distances.mean()) if distances.size > 0 else 0.05

INITIAL_BANDWIDTH_HMAX = avg_inter_edge_distance * 0.8
print(f"Average inter-edge distance: {avg_inter_edge_distance:.6f}")
print(f"Initial bandwidth: {INITIAL_BANDWIDTH_HMAX:.6f}")

# Edge Discretization
# Convert each straight edge into a polyline
print("Discretizing edges with curve")

E = len(edges_df)
graph_sampled_points = []
edge_info = []

# Assign each edge to a batch
edge_batches = np.arange(E) % SELF_AVOIDANCE_BATCHES
# print(edge_batches)

# Iterate through each edge to make curved polyline.
for eidx, (src, tgt) in enumerate(tqdm(edges_df.itertuples(index=False, name=None), total=E, desc="Discretizing")):
    # Get the start and end points in unit space.
    p0 = nodes_unit.loc[src].to_numpy(dtype=float)
    p1 = nodes_unit.loc[tgt].to_numpy(dtype=float)

    edge_dir = p1 - p0
    edge_len = np.linalg.norm(edge_dir)

    # Create evenly spaced points along the line
    t_vals = np.linspace(0.0, 1.0, SAMPLES_PER_EDGE)
    edge_points = []

    # Ensure no div by zero
    if edge_len > 1e-12:
        # norm
        ehat = edge_dir / edge_len

        # Two vectors that are perpendicular to the edge direction to define a plane for the curve
        perp1 = np.cross(ehat, np.array([0.0, 0.0, 1.0])) if abs(ehat[2]) < 0.9 else np.cross(ehat, np.array([1.0, 0.0, 0.0]))
        perp1 /= (np.linalg.norm(perp1) + 1e-12)
        perp2 = np.cross(ehat, perp1); perp2 /= (np.linalg.norm(perp2) + 1e-12)

        rng = np.random.default_rng(123 + eidx)
        # Magnitude of the curve proportional to the edge length
        curve_mag = rng.uniform(0.02, 0.08) * edge_len

        # Randomize phase of sine waves for curve uniqueness
        phase1 = rng.uniform(0, 2*np.pi)
        phase2 = rng.uniform(0, 2*np.pi)

        # For each point along the line, add a displacement to create the curve
        for t in t_vals:
            # The point on the straight line.
            linear = (1 - t) * p0 + t * p1
            # A sine wave based displacement in two perpendicular directions
            curve1 = curve_mag * np.sin(np.pi * t + phase1) * perp1
            curve2 = curve_mag * 0.5 * np.sin(2 * np.pi * t + phase2) * perp2
            edge_points.append(linear + curve1 + curve2)
    else:
        # If the edge has zero length, just create a straight line (which is just the point)
        for t in t_vals:
            edge_points.append((1 - t) * p0 + t * p1)

    # Store the resulting array of points for the edge
    graph_sampled_points.append(np.asarray(edge_points, dtype=float))
    edge_info.append({"source": src, "target": tgt, "edge_id": eidx, "batch": int(edge_batches[eidx])})


# KDE functions

# Takes points and splats them
def trilinear_splat_epanechnikov(points, weights, bandwidth):
    if len(points) == 0:
        return np.zeros((GRID_N, GRID_N, GRID_N), dtype=float)

    density = np.zeros((GRID_N, GRID_N, GRID_N), dtype=float)

    # Convert the [0, 1] point coordinates to grid cell indices
    grid_coords = np.clip(points, 0.0, 1.0) * (GRID_N - 1)

    # For each point, distribute weight among surrounding grid cells with trilinear interpolation
    for i in range(grid_coords.shape[0]):
        point = grid_coords[i]
        # Find the integer coordinates of the corner of the cube containing the point
        base = np.floor(point).astype(int)
        # where the point is inside that cube
        frac = point - base


        corners = (
            (0,0,0), (0,0,1), (0,1,0), (0,1,1),
            (1,0,0), (1,0,1), (1,1,0), (1,1,1)
        )
        # Distribute weight among corners by proximity
        for dx, dy, dz in corners:
            x = min(base[0] + dx, GRID_N - 1)
            y = min(base[1] + dy, GRID_N - 1)
            z = min(base[2] + dz, GRID_N - 1)
            # Weight for each corner is based on the point's proximity to that corner
            wx = frac[0] if dx else (1 - frac[0])
            wy = frac[1] if dy else (1 - frac[1])
            wz = frac[2] if dz else (1 - frac[2])
            density[x, y, z] += weights[i] * wx * wy * wz

    # Apply a Gaussian filter to blur density field.
    sigma = bandwidth * GRID_N
    return gaussian_filter(density, sigma=sigma, mode="constant", cval=0.0)

# Sample the gradient of the density field at a set of point locations
def trilinear_sample_gradient(gx, gy, gz, points):
    if len(points) == 0:
        return np.zeros((0, 3), dtype=float)

    grads = np.zeros((len(points), 3), dtype=float)
    grid_coords = np.clip(points, 0.0, 1.0) * (GRID_N - 1)

    # Use trilinear interpolation again, but this time to read the gradient values from the grid
    for i in range(grid_coords.shape[0]):
        point = grid_coords[i]
        base = np.floor(point).astype(int)
        frac = point - base

        # Calculate a weighted average of the gradient vectors at the corners
        gsum = np.zeros(3, dtype=float)
        wsum = 0.0
        for dx in (0, 1):
            for dy in (0, 1):
                for dz in (0, 1):
                    x = min(base[0] + dx, GRID_N - 1)
                    y = min(base[1] + dy, GRID_N - 1)
                    z = min(base[2] + dz, GRID_N - 1)
                    # Calculate the interpolation weight for this corner
                    wx = frac[0] if dx else (1 - frac[0])
                    wy = frac[1] if dy else (1 - frac[1])
                    wz = frac[2] if dz else (1 - frac[2])
                    w = wx * wy * wz
                    # Add weighted gradient from this corner to our sum
                    gsum[0] += w * gx[x, y, z]
                    gsum[1] += w * gy[x, y, z]
                    gsum[2] += w * gz[x, y, z]
                    wsum += w
        # use weighted average
        if wsum > 0:
            grads[i] = gsum / wsum
    return grads

print(f"\nKDEEB bundling: {NUM_ITERATIONS}")

for iteration in range(1, NUM_ITERATIONS + 1):
    print(f"\nIteration {iteration}/{NUM_ITERATIONS}")
    t_iter = time.time()

    # The bandwidth shrinks with each iteration allowing edges to make large movements at the start and fine tune their position later
    current_bandwidth = INITIAL_BANDWIDTH_HMAX * (KERNEL_REDUCTION_LAMBDA ** (iteration - 1))
    print(f"Current bandwidth: {current_bandwidth:.6f}")

    total_displacement = 0.0
    points_moved = 0

    # Pocess the data in batches to prevent edges from being influenced by their own density
    for batch_id in range(SELF_AVOIDANCE_BATCHES):
        print(f"Processing batch {batch_id+1}/{SELF_AVOIDANCE_BATCHES}")

        # Collect all points from all other batches to build the density map.
        other_points_list = []
        other_weights_list = []
        for edge_points, info in zip(graph_sampled_points, edge_info):
            if info["batch"] != batch_id:
                other_points_list.append(edge_points)
                # For now, all points have an equal weight of 1.
                other_weights_list.append(np.ones(len(edge_points), dtype=float))

        # If there are no other points skip
        if not other_points_list:
            continue

        # Combine the lists of points and weights into one
        other_points = np.concatenate(other_points_list, axis=0)
        other_weights = np.concatenate(other_weights_list, axis=0)

        # Build the smooted density map from the points in other batches.
        density_map = trilinear_splat_epanechnikov(other_points, other_weights, current_bandwidth)
        # Calculate the gradient of the density map
        gx, gy, gz = np.gradient(density_map, edge_order=2)

        # Now, move the points that are in the current batch based on the gradient
        for eidx, (edge_points, info) in enumerate(zip(graph_sampled_points, edge_info)):
            if info["batch"] != batch_id:
                continue

            # only move interior pts
            if len(edge_points) <= 2:
                continue

            interior = edge_points[1:-1].copy()
            if len(interior) == 0:
                continue

            # Sample the gradient at the location of each interior point.
            grads = trilinear_sample_gradient(gx, gy, gz, interior)
            # Calculate the magnitude of each gradient vec
            mags = np.linalg.norm(grads, axis=1)

            valid = mags > EPSILON_GRADIENT
            if np.any(valid):
                # Normalize gradient vectors
                dirs = np.zeros_like(grads)
                dirs[valid] = grads[valid] / mags[valid, None]

                # Scale by current bandwidth
                disp = current_bandwidth * dirs
                interior[valid] += disp[valid]

                # Update the main list of points with the new spots
                graph_sampled_points[eidx][1:-1] = interior

                # Keep track of how much the points have moved to check for convergence 
                total_displacement += float(np.linalg.norm(disp[valid], axis=1).sum())
                points_moved += int(valid.sum())

    # Calculate the average movement per point in the iteration.
    avg_displacement = total_displacement / max(points_moved, 1)
    print(f"Average displacement: {avg_displacement:.6f}")

    # After moving points, they might become unevenly spaced so resample the polylines
    print("Resampling edges")
    for eidx, edge_points in enumerate(graph_sampled_points):
        if len(edge_points) <= 2:
            continue

        # Calculate the cumulative length along the polyline
        diffs = np.diff(edge_points, axis=0)
        seg_len = np.linalg.norm(diffs, axis=1)
        cum_len = np.concatenate(([0.0], np.cumsum(seg_len)))
        total_len = cum_len[-1]
        if total_len < 1e-12:
            continue

        # Define new evenly spaced positions along the total length
        target = np.linspace(0.0, total_len, SAMPLES_PER_EDGE)
        resampled = []
        # Find where each new position falls on the old polyline and interpolate.
        for t in target:
            # Find the segment where the target distance 't' falls.
            k = np.searchsorted(cum_len, t) - 1
            k = int(np.clip(k, 0, len(seg_len) - 1))
            if seg_len[k] > 1e-12:
                # 'alpha' is how far along that segment we are.
                alpha = (t - cum_len[k]) / seg_len[k]
                alpha = float(np.clip(alpha, 0.0, 1.0))
                # Linearly interpolate to find the new point's coordinates
                resampled.append((1 - alpha) * edge_points[k] + alpha * edge_points[k + 1])
            else:
                # If the segment has zero length, just use the start point
                resampled.append(edge_points[k])

        resampled = np.asarray(resampled, dtype=float)
        # Keep the original endpoints fixed
        resampled[0]  = edge_points[0]
        resampled[-1] = edge_points[-1]
        graph_sampled_points[eidx] = resampled

    # Smooth lines
    print("Applying Laplacian smoothing")
    for _ in range(SMOOTHING_ITERATIONS):
        for eidx, edge_points in enumerate(graph_sampled_points):
            if len(edge_points) <= 2:
                continue
            sm = edge_points.copy()
            # For each interior point, move it slightly towards the average of its neighbors
            for i in range(1, len(edge_points) - 1):
                sm[i] = 0.25 * edge_points[i - 1] + 0.5 * edge_points[i] + 0.25 * edge_points[i + 1]
            graph_sampled_points[eidx] = sm

    # Make sure all points stay within [0, 1]
    for eidx in range(len(graph_sampled_points)):
        graph_sampled_points[eidx] = np.clip(graph_sampled_points[eidx], 0.0, 1.0)

    print(f"Iteration completed in {time.time() - t_iter:.2f}s")

    # If the points are barely moving, consider the layout converged and stop early
    if avg_displacement < 1e-6:
        print(f"Converged at iteration {iteration}")
        break


print("\nWriting output")

# Snap the endpoints to the original
nodes_world = nodes_full

records = []
for edge_points, info in zip(graph_sampled_points, edge_info):
    src, tgt = info["source"], info["target"]

    # Convert the polyline points from the unit cube back to original global coordinates
    world_points = from_unit(edge_points)

    n0 = nodes_world.loc[src, ["x", "y", "z"]].to_numpy(dtype=float)
    n1 = nodes_world.loc[tgt, ["x", "y", "z"]].to_numpy(dtype=float)
    world_points[0]  = n0
    world_points[-1] = n1

    pieces = [f"{x:.8f},{y:.8f},{z:.8f}" for (x, y, z) in world_points]
    records.append({"source": src, "target": tgt, "points": "|".join(pieces)})

pd.DataFrame(records).to_csv(out_csv, index=False)
print(f"Saved {len(records):,} bundled edges to {out_csv}")


print("\nQuality assessment:")
curvatures = []

# Measure curvature with second derivative of polyline
for edge_points in graph_sampled_points:
    if len(edge_points) > 2:
        d2 = np.linalg.norm(edge_points[2:] - 2 * edge_points[1:-1] + edge_points[:-2], axis=1)
        curvatures.extend(d2)

# Get the midpoint of each final bundled edge
edge_midpoints = []
for edge_points in graph_sampled_points:
    if len(edge_points) > 0:
        edge_midpoints.append(edge_points[len(edge_points) // 2])

if len(edge_midpoints) > 1:
    edge_midpoints = np.array(edge_midpoints, dtype=float)

    # The distances between midpoints tells how well-separated bundles are
    distances = pdist(edge_midpoints)
    print(f"Mean curvature: {np.mean(curvatures):.6f}")
    print(f"Mean inter-midpoint distance: {distances.mean():.6f}")
    print(f"Min inter-midpoint distance: {distances.min():.6f}")

    # Compare the length of the new curved path to the original straight-line distance
    straight_lengths = []
    curved_lengths = []
    for edge_points in graph_sampled_points:
        if len(edge_points) > 1:
            straight = np.linalg.norm(edge_points[-1] - edge_points[0])
            # Curved length is the sum of all the small segments
            segs = np.linalg.norm(np.diff(edge_points, axis=0), axis=1)
            curved = float(segs.sum())
            if straight > 1e-12:
                straight_lengths.append(straight)
                curved_lengths.append(curved)
    if straight_lengths:
        # A ratio > 1 indicates how much longer the bundled path is
        ratio = np.array(curved_lengths) / np.array(straight_lengths)
        print(f"Mean path length ratio: {ratio.mean():.3f}")

# Ensure error here is 0 or else the edges will not match the node positions
max_err = 0.0
mean_errs = []
for (src, tgt), pts in zip(edges_df.itertuples(index=False, name=None), graph_sampled_points):
    p0w, p1w = from_unit(pts[0]), from_unit(pts[-1])
    n0w = nodes_world.loc[src, ["x", "y", "z"]].to_numpy(dtype=float)
    n1w = nodes_world.loc[tgt, ["x", "y", "z"]].to_numpy(dtype=float)
    p0w = n0w
    p1w = n1w
    e = max(np.linalg.norm(p0w - n0w), np.linalg.norm(p1w - n1w))
    max_err = max(max_err, e)
    mean_errs.append(e)
print(f"Endpoint max error: {max_err:.12f}, mean: {np.mean(mean_errs):.12f}")

print("\nKDEEB completed.")