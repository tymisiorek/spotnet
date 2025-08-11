import time
from pathlib import Path
import os
import numpy as np
import pandas as pd
from dotenv import load_dotenv, find_dotenv
from scipy.ndimage import gaussian_filter
from scipy.spatial.distance import cdist, pdist
from tqdm import tqdm

# ---------------------- Hyperparams (following KDEEB paper more closely) ----------------------
NUM_ITERATIONS = 10                             # More iterations for convergence
INITIAL_BANDWIDTH_HMAX = 0.08                   # Start with reasonable bandwidth
KERNEL_REDUCTION_LAMBDA = 0.7                   # Reduce bandwidth each iteration
EPSILON_GRADIENT = 1e-5                         # Small value for gradient normalization
SMOOTHING_ITERATIONS = 3                        # Multiple smoothing passes
GRID_N = 80                                     # Higher resolution grid
TEST_SIZE = 15000                                # Smaller test for debugging
SAMPLES_PER_EDGE = 20                           # Fewer samples for speed
SELF_AVOIDANCE_BATCHES = 4                      # Self-avoidance batching

# ---------------------- IO ----------------------
load_dotenv(find_dotenv())
root_dir = Path(os.getenv("ROOT_DIR", Path(__file__).parent))
data_dir = root_dir / "data"

nodes_csv = data_dir / "network_nodes.csv"
edges_csv = data_dir / "network_edges.csv"
out_csv   = data_dir / "edges_bundled_3d_test.csv"

print(f"Loading data...")
nodes_df = (pd.read_csv(nodes_csv, usecols=["id","x","y","z"])
              .set_index("id")
              .astype({"x":"float32","y":"float32","z":"float32"}))
nodes_df.index = nodes_df.index.astype("string")

edges_df = pd.read_csv(edges_csv, usecols=["source","target"])
edges_df[["source","target"]] = edges_df[["source","target"]].astype("string")
edges_df.index.name = "edge_id"

# Create test dataset
print(f"Original dataset: {len(edges_df):,} edges")
if len(edges_df) > TEST_SIZE:
    test_edges = edges_df.sample(n=TEST_SIZE, random_state=42)
    test_nodes = set(test_edges["source"].unique()) | set(test_edges["target"].unique())
    nodes_df = nodes_df.loc[list(test_nodes)]
    test_edges = test_edges[
        test_edges["source"].isin(test_nodes) & 
        test_edges["target"].isin(test_nodes)
    ]
    edges_df = test_edges.reset_index(drop=True)
    print(f"Test dataset: {len(edges_df):,} edges, {len(nodes_df):,} nodes")

# ---------------------- Normalization ----------------------
mins = nodes_df[["x","y","z"]].min()
spans = (nodes_df[["x","y","z"]].max() - mins).replace(0, 1.0)
to_unit   = lambda arr: (arr - mins.values) / spans.values
from_unit = lambda arr: arr * spans.values + mins.values
nodes_unit = to_unit(nodes_df[["x","y","z"]])

# ---------------------- Calculate Initial Bandwidth from Data ----------------------
print("Calculating initial bandwidth from inter-edge distances...")
edge_centers = []
for src, tgt in edges_df.itertuples(index=False, name=None):
    p0 = nodes_unit.loc[src].values
    p1 = nodes_unit.loc[tgt].values
    center = (p0 + p1) / 2.0
    edge_centers.append(center)

edge_centers = np.array(edge_centers)

# Calculate average distance between edge centers (sample for large datasets)
if len(edge_centers) > 1000:
    sample_idx = np.random.choice(len(edge_centers), 1000, replace=False)
    sample_centers = edge_centers[sample_idx]
    distances = pdist(sample_centers)
else:
    distances = pdist(edge_centers)

avg_inter_edge_distance = distances.mean()
INITIAL_BANDWIDTH_HMAX = avg_inter_edge_distance * 0.8  # Scale factor for bundling strength
print(f"Average inter-edge distance: {avg_inter_edge_distance:.6f}")
print(f"Initial bandwidth: {INITIAL_BANDWIDTH_HMAX:.6f}")

# ---------------------- Step 1: Discretize Edges with Better Initialization ----------------------
print("Step 1: Discretizing edges with curved initialization...")
E = len(edges_df)

graph_sampled_points = []
edge_info = []
edge_batches = np.arange(E) % SELF_AVOIDANCE_BATCHES  # For self-avoidance

for eidx, (src, tgt) in enumerate(tqdm(edges_df.itertuples(index=False, name=None), total=E, desc="Discretizing")):
    p0 = nodes_unit.loc[src].values
    p1 = nodes_unit.loc[tgt].values
    
    # Create initial curved path (important for bundling!)
    edge_dir = p1 - p0
    edge_length = np.linalg.norm(edge_dir)
    
    # Generate sample points with initial random curvature
    t_vals = np.linspace(0.0, 1.0, SAMPLES_PER_EDGE)
    edge_points = []
    
    # Create two perpendicular vectors to edge
    if edge_length > 1e-8:
        edge_unit = edge_dir / edge_length
        # Find perpendicular vectors
        if abs(edge_unit[2]) < 0.9:
            perp1 = np.cross(edge_unit, np.array([0, 0, 1]))
        else:
            perp1 = np.cross(edge_unit, np.array([1, 0, 0]))
        perp1 = perp1 / np.linalg.norm(perp1)
        perp2 = np.cross(edge_unit, perp1)
        perp2 = perp2 / np.linalg.norm(perp2)
        
        # Random curve parameters
        np.random.seed(eidx + 42)  # Reproducible but different per edge
        curve_mag = np.random.uniform(0.02, 0.08) * edge_length
        phase1 = np.random.uniform(0, 2*np.pi)
        phase2 = np.random.uniform(0, 2*np.pi)
        
        for t in t_vals:
            # Linear interpolation
            linear_pos = (1-t) * p0 + t * p1
            
            # Add sinusoidal curves in two perpendicular directions
            curve1 = curve_mag * np.sin(np.pi * t + phase1) * perp1
            curve2 = curve_mag * 0.5 * np.sin(2 * np.pi * t + phase2) * perp2
            
            curved_pos = linear_pos + curve1 + curve2
            edge_points.append(curved_pos)
    else:
        # Degenerate edge
        for t in t_vals:
            edge_points.append((1-t) * p0 + t * p1)
    
    edge_points = np.array(edge_points, dtype=np.float32)
    graph_sampled_points.append(edge_points)
    edge_info.append({"source": src, "target": tgt, "edge_id": eidx, "batch": edge_batches[eidx]})

# ---------------------- Proper Trilinear Splatting and Sampling ----------------------
def trilinear_splat_epanechnikov(points, weights, bandwidth):
    """Proper trilinear splatting with Epanechnikov kernel approximation"""
    if len(points) == 0:
        return np.zeros((GRID_N, GRID_N, GRID_N), dtype=np.float32)
    
    density = np.zeros((GRID_N, GRID_N, GRID_N), dtype=np.float32)
    
    # Convert to grid coordinates
    grid_coords = np.clip(points, 0.0, 1.0) * (GRID_N - 1)
    
    for i, point in enumerate(grid_coords):
        # Get base grid cell and fractional part
        base = np.floor(point).astype(int)
        frac = point - base
        
        # Trilinear weights
        weights_3d = [
            [(1-frac[0])*(1-frac[1])*(1-frac[2]), base[0], base[1], base[2]],
            [(1-frac[0])*(1-frac[1])*frac[2],     base[0], base[1], min(base[2]+1, GRID_N-1)],
            [(1-frac[0])*frac[1]*(1-frac[2]),     base[0], min(base[1]+1, GRID_N-1), base[2]],
            [(1-frac[0])*frac[1]*frac[2],         base[0], min(base[1]+1, GRID_N-1), min(base[2]+1, GRID_N-1)],
            [frac[0]*(1-frac[1])*(1-frac[2]),     min(base[0]+1, GRID_N-1), base[1], base[2]],
            [frac[0]*(1-frac[1])*frac[2],         min(base[0]+1, GRID_N-1), base[1], min(base[2]+1, GRID_N-1)],
            [frac[0]*frac[1]*(1-frac[2]),         min(base[0]+1, GRID_N-1), min(base[1]+1, GRID_N-1), base[2]],
            [frac[0]*frac[1]*frac[2],             min(base[0]+1, GRID_N-1), min(base[1]+1, GRID_N-1), min(base[2]+1, GRID_N-1)]
        ]
        
        # Add contribution to each corner
        for w, x, y, z in weights_3d:
            if 0 <= x < GRID_N and 0 <= y < GRID_N and 0 <= z < GRID_N:
                density[x, y, z] += w * weights[i]
    
    # Apply Gaussian blur to approximate Epanechnikov kernel
    sigma = bandwidth * GRID_N
    density = gaussian_filter(density, sigma=sigma, mode='constant', cval=0.0)
    
    return density

def trilinear_sample_gradient(gx, gy, gz, points):
    """Sample gradient field at points using trilinear interpolation"""
    if len(points) == 0:
        return np.zeros((0, 3))
    
    gradients = np.zeros((len(points), 3))
    grid_coords = np.clip(points, 0.0, 1.0) * (GRID_N - 1)
    
    for i, point in enumerate(grid_coords):
        base = np.floor(point).astype(int)
        frac = point - base
        
        # Trilinear interpolation
        gradient_sum = np.zeros(3)
        total_weight = 0.0
        
        for dx in [0, 1]:
            for dy in [0, 1]:
                for dz in [0, 1]:
                    x = np.clip(base[0] + dx, 0, GRID_N-1)
                    y = np.clip(base[1] + dy, 0, GRID_N-1)
                    z = np.clip(base[2] + dz, 0, GRID_N-1)
                    
                    wx = frac[0] if dx else (1-frac[0])
                    wy = frac[1] if dy else (1-frac[1])
                    wz = frac[2] if dz else (1-frac[2])
                    weight = wx * wy * wz
                    
                    gradient_sum[0] += weight * gx[x, y, z]
                    gradient_sum[1] += weight * gy[x, y, z]
                    gradient_sum[2] += weight * gz[x, y, z]
                    total_weight += weight
        
        if total_weight > 0:
            gradients[i] = gradient_sum / total_weight
    
    return gradients

# ---------------------- Proper KDEEB Algorithm ----------------------
print(f"\nStep 2: Proper KDEEB bundling with {NUM_ITERATIONS} iterations...")

for iteration in range(1, NUM_ITERATIONS + 1):
    print(f"\n--- Iteration {iteration}/{NUM_ITERATIONS} ---")
    t_iter = time.time()
    
    # Calculate current bandwidth (following KDEEB formula)
    current_bandwidth = INITIAL_BANDWIDTH_HMAX * (KERNEL_REDUCTION_LAMBDA ** (iteration - 1))
    print(f"Current bandwidth: {current_bandwidth:.6f}")
    
    total_displacement = 0.0
    points_moved = 0
    
    # Self-avoidance: process each batch separately
    for batch_id in range(SELF_AVOIDANCE_BATCHES):
        print(f"  Processing batch {batch_id+1}/{SELF_AVOIDANCE_BATCHES}...")
        
        # 2.1 Build density map from OTHER batches only
        other_points = []
        other_weights = []
        
        for edge_idx, (edge_points, info) in enumerate(zip(graph_sampled_points, edge_info)):
            if info["batch"] != batch_id:  # Exclude current batch
                for point in edge_points:
                    other_points.append(point)
                    other_weights.append(1.0)  # Equal weight for all points
        
        if len(other_points) == 0:
            continue
            
        other_points = np.array(other_points)
        other_weights = np.array(other_weights)
        
        # Build density map
        density_map = trilinear_splat_epanechnikov(other_points, other_weights, current_bandwidth)
        
        # 2.2 Compute gradient
        gx, gy, gz = np.gradient(density_map, edge_order=2)
        
        # 2.3 Advect points in current batch
        for edge_idx, (edge_points, info) in enumerate(zip(graph_sampled_points, edge_info)):
            if info["batch"] != batch_id:
                continue
                
            # Only move interior points (keep endpoints fixed)
            interior_points = edge_points[1:-1].copy()
            
            if len(interior_points) == 0:
                continue
            
            # Sample gradient at interior points
            gradients = trilinear_sample_gradient(gx, gy, gz, interior_points)
            
            # KDEEB gradient normalization: normalize by magnitude, not density
            gradient_mags = np.linalg.norm(gradients, axis=1)
            
            # Only move points with significant gradients
            valid_mask = gradient_mags > EPSILON_GRADIENT
            
            if np.any(valid_mask):
                # Normalize gradients (KDEEB way)
                normalized_grads = gradients.copy()
                normalized_grads[valid_mask] = gradients[valid_mask] / gradient_mags[valid_mask, np.newaxis]
                
                # Calculate displacement (KDEEB formula)
                displacements = current_bandwidth * normalized_grads
                
                # Apply displacements
                interior_points[valid_mask] += displacements[valid_mask]
                
                # Update edge points
                graph_sampled_points[edge_idx][1:-1] = interior_points
                
                # Track movement
                displacement_mags = np.linalg.norm(displacements[valid_mask], axis=1)
                total_displacement += displacement_mags.sum()
                points_moved += len(displacement_mags)
    
    avg_displacement = total_displacement / max(points_moved, 1)
    print(f"  Average displacement: {avg_displacement:.6f}")
    
    # 2.4 Resample edges to maintain uniform spacing
    print("  Resampling edges...")
    for edge_idx, edge_points in enumerate(graph_sampled_points):
        if len(edge_points) <= 2:
            continue
            
        # Calculate cumulative arc length
        diffs = np.diff(edge_points, axis=0)
        segment_lengths = np.linalg.norm(diffs, axis=1)
        cumulative_lengths = np.concatenate([[0], np.cumsum(segment_lengths)])
        total_length = cumulative_lengths[-1]
        
        if total_length < 1e-8:
            continue
        
        # Resample at uniform intervals
        uniform_lengths = np.linspace(0, total_length, SAMPLES_PER_EDGE)
        resampled_points = []
        
        for target_length in uniform_lengths:
            # Find segment containing this length
            segment_idx = np.searchsorted(cumulative_lengths, target_length) - 1
            segment_idx = np.clip(segment_idx, 0, len(segment_lengths) - 1)
            
            if segment_idx >= len(segment_lengths):
                resampled_points.append(edge_points[-1])
            else:
                # Interpolate within segment
                segment_start_length = cumulative_lengths[segment_idx]
                segment_length = segment_lengths[segment_idx]
                
                if segment_length > 1e-8:
                    t = (target_length - segment_start_length) / segment_length
                    t = np.clip(t, 0.0, 1.0)
                    interpolated = (1-t) * edge_points[segment_idx] + t * edge_points[segment_idx + 1]
                    resampled_points.append(interpolated)
                else:
                    resampled_points.append(edge_points[segment_idx])
        
        graph_sampled_points[edge_idx] = np.array(resampled_points)
    
    # 2.5 Laplacian smoothing
    print("  Applying Laplacian smoothing...")
    for _ in range(SMOOTHING_ITERATIONS):
        for edge_idx, edge_points in enumerate(graph_sampled_points):
            if len(edge_points) <= 2:
                continue
                
            smoothed = edge_points.copy()
            
            # Apply smoothing to interior points only
            for i in range(1, len(edge_points) - 1):
                # 3-point Laplacian smoothing
                smoothed[i] = 0.25 * edge_points[i-1] + 0.5 * edge_points[i] + 0.25 * edge_points[i+1]
            
            graph_sampled_points[edge_idx] = smoothed
    
    # Clip to unit cube
    for edge_idx in range(len(graph_sampled_points)):
        graph_sampled_points[edge_idx] = np.clip(graph_sampled_points[edge_idx], 0.0, 1.0)
    
    print(f"  Iteration completed in {time.time() - t_iter:.2f}s")
    
    # Early stopping if converged
    if avg_displacement < 1e-6:
        print(f"  Converged at iteration {iteration}")
        break

# ---------------------- Final Output ----------------------
print("\nStep 3: Writing final output...")
records = []

for edge_idx, (edge_points, info) in enumerate(zip(graph_sampled_points, edge_info)):
    # Convert back to world coordinates
    world_points = from_unit(edge_points).astype(np.float32)
    
    # Format points as string
    pieces = [f"{x:.6f},{y:.6f},{z:.6f}" for (x, y, z) in world_points]
    
    records.append({
        "source": info["source"], 
        "target": info["target"], 
        "points": "|".join(pieces)
    })

pd.DataFrame(records).to_csv(out_csv, index=False)
print(f"Saved {len(records):,} bundled edges → {out_csv}")

# Quality assessment
print("\nFinal quality assessment:")
curvatures = []
bundling_distances = []

for edge_points in graph_sampled_points:
    if len(edge_points) > 2:
        # Compute curvature
        d2 = np.linalg.norm(edge_points[2:] - 2*edge_points[1:-1] + edge_points[:-2], axis=1)
        curvatures.extend(d2)

# Check bundling by looking at midpoint clustering
edge_midpoints = []
for edge_points in graph_sampled_points:
    if len(edge_points) > 0:
        mid_idx = len(edge_points) // 2
        edge_midpoints.append(edge_points[mid_idx])

if len(edge_midpoints) > 1:
    edge_midpoints = np.array(edge_midpoints)
    distances = pdist(edge_midpoints)
    
    print(f"Mean curvature: {np.mean(curvatures):.6f}")
    print(f"Mean inter-midpoint distance: {distances.mean():.6f}")
    print(f"Min inter-midpoint distance: {distances.min():.6f}")
    
    # Path length ratio
    straight_lengths = []
    curved_lengths = []
    
    for edge_points in graph_sampled_points:
        straight_len = np.linalg.norm(edge_points[-1] - edge_points[0])
        
        curved_len = 0.0
        for i in range(1, len(edge_points)):
            curved_len += np.linalg.norm(edge_points[i] - edge_points[i-1])
        
        if straight_len > 1e-8:
            straight_lengths.append(straight_len)
            curved_lengths.append(curved_len)
    
    if straight_lengths:
        length_ratios = np.array(curved_lengths) / np.array(straight_lengths)
        print(f"Mean path length ratio: {length_ratios.mean():.3f}")

print(f"\nProper KDEEB completed! Check {out_csv} for results.")