import time
from pathlib import Path
import os
import numpy as np
import pandas as pd
from dotenv import load_dotenv, find_dotenv
from scipy.ndimage import gaussian_filter
from scipy.spatial.distance import cdist, pdist
from tqdm import tqdm

# ---------------------- Hyperparams (tunable) ----------------------
GRID_N = 64                                     # Smaller grid for faster testing
SIGMAS = [10.0, 6.0, 4.0, 2.5, 1.5]            # Even smaller sigmas for tighter bundling
ITERS_PER_STAGE = [8, 12, 16, 20, 24]          # More iterations for better convergence
STEP_ETA = 1.0                                  # More aggressive step size
LAPLACE_LAMBDA = 0.02                           # Lighter smoothing to preserve curves
SAMPLES_PER_EDGE = 32                           # More samples for smoother curves
EPS_DENS = 1e-8                                 # Smaller epsilon
BATCHES = 16                                     # Fewer batches for testing
TAN_SUPPRESS = 0.3                              # Weaker tangent suppression to allow more curvature
JITTER_VOXELS = 2.0                             # More initial jitter
ENDPOINT_MASS_SCALE = 0.02                      # Even lower endpoint contribution
EXTRA_GRAD_EPS = 0.0                            # No extra gradient
MIN_DENSITY_THRESH = 1e-7                       # Lower density threshold
CHUNK_SIZE = 2000                               # Smaller chunks for memory efficiency
TEST_SIZE = 5000                                # Number of edges to test with

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

# Create smaller test dataset
print(f"Original dataset: {len(edges_df):,} edges")
if len(edges_df) > TEST_SIZE:
    # Sample edges randomly but ensure we keep connected components
    test_edges = edges_df.sample(n=TEST_SIZE, random_state=42)
    
    # Get all unique nodes in the test edges
    test_nodes = set(test_edges["source"].unique()) | set(test_edges["target"].unique())
    
    # Filter nodes dataframe to only include test nodes
    nodes_df = nodes_df.loc[list(test_nodes)]
    
    # Update edges to only include those where both source and target are in our node set
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

# ---------------------- Build polylines ----------------------
E = len(edges_df)
S = SAMPLES_PER_EDGE
pts = np.empty((E, S, 3), dtype=np.float32)

print(f"Building {E:,} polylines with {S} samples each...")
for eidx, (src, tgt) in tqdm(
        enumerate(edges_df.itertuples(index=False, name=None), start=0),
        total=E, desc="Init polylines"):
    p0 = nodes_unit.loc[src].values
    p1 = nodes_unit.loc[tgt].values
    ts = np.linspace(0.0, 1.0, S, dtype=np.float32)[:, None]
    pts[eidx] = (1 - ts) * p0 + ts * p1

fixed_mask = np.zeros((E, S), dtype=bool)
fixed_mask[:, 0] = True
fixed_mask[:, -1] = True

# Edge-length weights
src_xyz = nodes_unit.loc[edges_df["source"]].values
tgt_xyz = nodes_unit.loc[edges_df["target"]].values
edge_lengths = np.linalg.norm(tgt_xyz - src_xyz, axis=1).astype(np.float32)
sample_mass = (edge_lengths / S).astype(np.float32)[:, None]
sample_mass = np.broadcast_to(sample_mass, (E, S)).copy()

# Better initialization with stronger curvature and jitter
print("Initializing curved paths...")
np.random.seed(42)  # Different seed for different patterns
voxel_size_world = 1.0 / (GRID_N - 1)
jitter_std = (JITTER_VOXELS * voxel_size_world)

# Create more pronounced curved initialization
for eidx in tqdm(range(E), desc="Creating curves"):
    p0 = pts[eidx, 0]
    p1 = pts[eidx, -1]
    
    # Create two perpendicular directions for more complex curves
    direction = p1 - p0
    perp1 = np.array([-direction[1], direction[0], 0]) if direction[0] != 0 or direction[1] != 0 else np.array([0, 0, 1])
    perp1 = perp1 / (np.linalg.norm(perp1) + 1e-8)
    
    # Second perpendicular direction
    perp2 = np.cross(direction, perp1)
    perp2 = perp2 / (np.linalg.norm(perp2) + 1e-8)
    
    # Much stronger curve magnitudes for more pronounced curves
    curve_mag1 = np.random.uniform(-0.3, 0.3)
    curve_mag2 = np.random.uniform(-0.25, 0.25)
    
    # Apply cubic curve for more natural paths
    t_vals = np.linspace(0, 1, S)
    for i in range(1, S-1):
        t = t_vals[i]
        # Cubic curve with two perpendicular components
        curve_offset1 = 4 * t * (1 - t) * curve_mag1 * perp1
        curve_offset2 = 4 * t * (1 - t) * curve_mag2 * perp2
        pts[eidx, i] = (1-t) * p0 + t * p1 + curve_offset1 + curve_offset2

# Add stronger random jitter
pts[:, 1:-1, :] += np.random.normal(0.0, jitter_std, size=pts[:, 1:-1, :].shape).astype(np.float32)
np.clip(pts, 0.0, 1.0, out=pts)

# Assign batches for self-density exclusion
edge_batch = (np.arange(E) % BATCHES).astype(np.int32)

# ---------------------- Helpers ----------------------
def voxelise(p):
    v = np.clip(p, 0.0, 1.0) * (GRID_N - 1)
    base = np.floor(v).astype(np.int32)
    frac = v - base
    return base, frac

def trilinear_splat(points, mass=None):
    N = points.shape[0]
    if N == 0:
        return np.zeros((GRID_N, GRID_N, GRID_N), dtype=np.float32)
    if mass is None:
        mass = np.ones((N,), dtype=np.float32)
    base, frac = voxelise(points)
    fx, fy, fz = frac.T
    density = np.zeros((GRID_N, GRID_N, GRID_N), dtype=np.float32)

    for corner in range(8):
        cx = (corner & 1)
        cy = (corner >> 1) & 1
        cz = (corner >> 2) & 1

        wx = fx if cx else (1 - fx)
        wy = fy if cy else (1 - fy)
        wz = fz if cz else (1 - fz)
        w = (wx * wy * wz) * mass

        ix = np.clip(base[:, 0] + cx, 0, GRID_N - 1)
        iy = np.clip(base[:, 1] + cy, 0, GRID_N - 1)
        iz = np.clip(base[:, 2] + cz, 0, GRID_N - 1)

        np.add.at(density, (ix, iy, iz), w.astype(np.float32))

    return density

def trilinear_sample(volume, points):
    if points.shape[0] == 0:
        return np.zeros((0,), dtype=np.float32)
    base, frac = voxelise(points)
    fx, fy, fz = frac.T
    out = np.zeros((points.shape[0],), dtype=np.float32)

    for corner in range(8):
        cx = (corner & 1)
        cy = (corner >> 1) & 1
        cz = (corner >> 2) & 1

        wx = fx if cx else (1 - fx)
        wy = fy if cy else (1 - fy)
        wz = fz if cz else (1 - fz)
        w = (wx * wy * wz)

        ix = np.clip(base[:, 0] + cx, 0, GRID_N - 1)
        iy = np.clip(base[:, 1] + cy, 0, GRID_N - 1)
        iz = np.clip(base[:, 2] + cz, 0, GRID_N - 1)

        out += volume[ix, iy, iz] * w.astype(np.float32)

    return out

def trilinear_sample_vec3(field, points):
    if points.shape[0] == 0:
        return np.zeros((0,3), dtype=np.float32)
    base, frac = voxelise(points)
    fx, fy, fz = frac.T
    out = np.zeros((points.shape[0], 3), dtype=np.float32)

    for corner in range(8):
        cx = (corner & 1)
        cy = (corner >> 1) & 1
        cz = (corner >> 2) & 1

        wx = fx if cx else (1 - fx)
        wy = fy if cy else (1 - fy)
        wz = fz if cz else (1 - fz)
        w = (wx * wy * wz)[:, None]

        ix = np.clip(base[:, 0] + cx, 0, GRID_N - 1)
        iy = np.clip(base[:, 1] + cy, 0, GRID_N - 1)
        iz = np.clip(base[:, 2] + cz, 0, GRID_N - 1)

        out += field[ix, iy, iz] * w.astype(np.float32)

    return out

# ---------------------- KDEEB loop ----------------------
print(f"\nStarting bundling with {E:,} edges...")
for stage, (sigma, niter) in enumerate(zip(SIGMAS, ITERS_PER_STAGE), start=1):
    print(f"\n=== Stage {stage}: σ={sigma:.1f}, {niter} iterations ===")
    
    for it in range(1, niter + 1):
        t_iter = time.time()
        total_movement = 0.0
        moved_points = 0

        for b in range(BATCHES):
            # Build density from all edges NOT in batch b
            other_edges = (edge_batch != b)
            if not np.any(other_edges):
                continue

            # Process in chunks to avoid memory issues with large datasets
            other_indices = np.where(other_edges)[0]
            density = np.zeros((GRID_N, GRID_N, GRID_N), dtype=np.float32)
            
            # Accumulate density from chunks
            for chunk_start in range(0, len(other_indices), CHUNK_SIZE):
                chunk_end = min(chunk_start + CHUNK_SIZE, len(other_indices))
                chunk_indices = other_indices[chunk_start:chunk_end]
                
                chunk_pts = pts[chunk_indices].reshape(-1, 3)
                chunk_mass = sample_mass[chunk_indices].copy()
                
                # Scale endpoint mass
                chunk_mass[:, 0] *= ENDPOINT_MASS_SCALE
                chunk_mass[:, -1] *= ENDPOINT_MASS_SCALE
                
                chunk_density = trilinear_splat(chunk_pts, mass=chunk_mass.reshape(-1))
                density += chunk_density

            # Smooth (KDE approximation) with better boundary handling
            rho = gaussian_filter(density, sigma=sigma, mode="reflect", cval=0.0)
            
            # Compute gradient field with improved precision
            gx, gy, gz = np.gradient(rho, edge_order=1)  # First order for stability
            grad = np.stack([gx, gy, gz], axis=-1).astype(np.float32)
            
            # Normalize gradient magnitudes for better convergence
            grad_mag = np.linalg.norm(grad, axis=-1, keepdims=True)
            grad_mag[grad_mag == 0] = 1.0
            grad = grad / grad_mag

            # Advect current batch (also in chunks if needed)
            cur_edges = (edge_batch == b)
            if not np.any(cur_edges):
                continue

            cur_indices = np.where(cur_edges)[0]
            
            # Process current batch in chunks
            for chunk_start in range(0, len(cur_indices), CHUNK_SIZE):
                chunk_end = min(chunk_start + CHUNK_SIZE, len(cur_indices))
                chunk_indices = cur_indices[chunk_start:chunk_end]
                
                sub = pts[chunk_indices].copy()
                move_mask_sub = ~fixed_mask[chunk_indices]

                # Sample density and gradient at current positions
                cur_pts = sub.reshape(-1, 3)
                g = trilinear_sample_vec3(grad, cur_pts)
                r = trilinear_sample(rho, cur_pts)

                # Only move points where there's sufficient density
                valid_move = r > MIN_DENSITY_THRESH
                
                # Improved mean-shift formula with density weighting
                mean_shift = np.zeros_like(g)
                valid_idx = valid_move & (r > EPS_DENS)
                
                if np.any(valid_idx):
                    # Use density-weighted gradient for better bundling
                    density_weight = np.clip(r[valid_idx] / (r.max() + EPS_DENS), 0.1, 1.0)
                    mean_shift[valid_idx] = g[valid_idx] * density_weight[:, None] * sigma

                # Adaptive step size with better scaling
                step_scale = np.clip(r / (r.max() + EPS_DENS), 0.1, 1.0)
                max_step = STEP_ETA * voxel_size_world * sigma * step_scale[:, None]
                
                step_len = np.linalg.norm(mean_shift, axis=1, keepdims=True)
                step_len[step_len == 0] = 1.0
                delta = mean_shift * np.minimum(max_step / step_len, 1.0)
                delta = delta.reshape(sub.shape)

                # Enhanced tangent suppression and density attraction
                if TAN_SUPPRESS > 0:
                    tangent = np.zeros_like(sub)
                    tangent[:, 1:-1, :] = sub[:, 2:, :] - sub[:, :-2, :]
                    tnorm = np.linalg.norm(tangent, axis=2, keepdims=True)
                    tnorm[tnorm == 0] = 1.0
                    that = tangent / tnorm
                    
                    # Project delta onto tangent and subtract (weaker suppression for more curvature)
                    tan_component = (delta * that).sum(axis=2, keepdims=True) * that
                    delta = delta - TAN_SUPPRESS * tan_component
                
                # Add curvature preservation force to maintain initial curves
                curvature_force = np.zeros_like(delta)
                for i in range(1, S-1):  # Skip endpoints
                    # Compute current curvature at this point
                    if i > 0 and i < S-1:
                        prev_point = sub[:, i-1, :]
                        curr_point = sub[:, i, :]
                        next_point = sub[:, i+1, :]
                        
                        # Compute curvature as deviation from straight line
                        straight_line = 0.5 * (prev_point + next_point)
                        curvature = curr_point - straight_line
                        
                        # Apply curvature preservation force
                        curvature_force[:, i] = 0.1 * curvature
                
                # Add curvature preservation
                delta = delta + curvature_force
                
                # Add density-based attraction force
                density_attraction = np.zeros_like(delta)
                for i in range(1, S-1):  # Skip endpoints
                    # Sample density at current and neighboring positions
                    current_density = r.reshape(sub.shape[0], S)[:, i]
                    
                    # Create small perturbations to find gradient direction
                    eps = voxel_size_world * 0.1
                    perturbations = np.array([
                        [eps, 0, 0], [-eps, 0, 0],
                        [0, eps, 0], [0, -eps, 0],
                        [0, 0, eps], [0, 0, -eps]
                    ])
                    
                    for j, pert in enumerate(perturbations):
                        pert_pts = cur_pts.reshape(sub.shape[0], S, 3)[:, i] + pert
                        pert_density = trilinear_sample(rho, pert_pts)
                        density_diff = pert_density - current_density
                        
                        # Move toward higher density (handle array comparison)
                        positive_diff_mask = density_diff > 0
                        if np.any(positive_diff_mask):
                            # Broadcast pert to match the number of points with positive density difference
                            pert_broadcast = pert[None, :]  # Shape: (1, 3)
                            density_attraction[positive_diff_mask, i] += pert_broadcast * density_diff[positive_diff_mask, None] * 0.2
                
                # Combine mean-shift and density attraction with stronger attraction
                delta = delta + 0.5 * density_attraction

                # Track movement for convergence monitoring
                move_magnitude = np.linalg.norm(delta[move_mask_sub], axis=-1)
                total_movement += move_magnitude.sum()
                moved_points += move_magnitude.shape[0]

                # Apply movement
                sub[move_mask_sub] += delta[move_mask_sub]

                # Laplacian smoothing for regularity
                if LAPLACE_LAMBDA > 0:
                    mid = sub[:, 1:-1, :].copy()
                    avg = 0.5 * (sub[:, :-2, :] + sub[:, 2:, :])
                    sub[:, 1:-1, :] = (1.0 - LAPLACE_LAMBDA) * mid + LAPLACE_LAMBDA * avg

                # Clamp to unit cube
                np.clip(sub, 0.0, 1.0, out=sub)
                pts[chunk_indices] = sub

        # Progress reporting with more diagnostics
        avg_movement = total_movement / max(moved_points, 1)
        
        # Compute current bundling quality
        edge_centers = pts[:, S//2, :]
        if len(edge_centers) > 1000:
            # Sample for efficiency
            sample_idx = np.random.choice(len(edge_centers), 1000, replace=False)
            sample_centers = edge_centers[sample_idx]
        else:
            sample_centers = edge_centers
        
        # Compute average distance to nearest neighbor
        if len(sample_centers) > 1:
            distances = cdist(sample_centers, sample_centers)
            np.fill_diagonal(distances, np.inf)
            min_distances = distances.min(axis=1)
            avg_min_dist = min_distances.mean()
        else:
            avg_min_dist = 0.0
        
        print(f"  σ={sigma:>4.1f} it {it:02d}/{niter}  avg_move={avg_movement:.6f}  avg_min_dist={avg_min_dist:.4f}  elapsed={time.time()-t_iter:.2f}s")

        # Early stopping if converged
        if avg_movement < 1e-6:
            print(f"  Converged at iteration {it}")
            break

    # Stage completion diagnostics
    d2 = np.linalg.norm(pts[:, 2:, :] - 2*pts[:, 1:-1, :] + pts[:, :-2, :], axis=2)
    curvature = d2.mean()
    print(f"Stage {stage} completed - mean curvature: {curvature:.6f}")
    
    # Check for actual bundling (sample-based for large datasets)
    if E > 10000:
        # Sample subset for memory efficiency
        sample_size = min(5000, E)
        sample_idx = np.random.choice(E, sample_size, replace=False)
        edge_centers = pts[sample_idx, S//2, :]
        print(f"Sampling {sample_size} edges for bundling assessment...")
    else:
        edge_centers = pts[:, S//2, :]
    
    # Compute pairwise distances efficiently
    pairwise_dist = pdist(edge_centers)
    min_dist = pairwise_dist.min() if len(pairwise_dist) > 0 else 0.0
    print(f"Minimum distance between edge midpoints: {min_dist:.6f}")

# ---------------------- Write CSV ----------------------
print("\nWriting bundled edges …")
records = []
for eidx, (src, tgt) in enumerate(edges_df.itertuples(index=False, name=None), start=0):
    poly = from_unit(pts[eidx]).astype(np.float32)
    pieces = [f"{x:.6f},{y:.6f},{z:.6f}" for (x, y, z) in poly]
    records.append({"source": src, "target": tgt, "points": "|".join(pieces)})

pd.DataFrame(records).to_csv(out_csv, index=False)
print(f"Saved {len(records):,} bundled edges → {out_csv}")

# Final quality assessment
final_curvature = np.linalg.norm(pts[:, 2:, :] - 2*pts[:, 1:-1, :] + pts[:, :-2, :], axis=2).mean()
print(f"Final mean curvature: {final_curvature:.6f}")
straight_line_lengths = np.linalg.norm(pts[:, -1, :] - pts[:, 0, :], axis=1)
bundled_lengths = np.sum(np.linalg.norm(pts[:, 1:, :] - pts[:, :-1, :], axis=2), axis=1)
length_ratio = bundled_lengths / straight_line_lengths
print(f"Mean path length ratio (curved/straight): {length_ratio.mean():.3f}")

print(f"\nTest completed! Check {out_csv} for results.")
