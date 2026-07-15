"""Neural + point-cloud tools — text/image → mesh, and point cloud reconstruction.

Two very different worlds bundled here because they share downstream mesh outputs:
  1. Shap-E: OpenAI's text-to-3D and image-to-3D diffusion model.
  2. Point-cloud → mesh: reconstruction from unstructured points (ball-pivoting/Poisson-lite).
"""
from __future__ import annotations
import time
from pathlib import Path
from typing import Any

from strands import tool
from strands_cad._common import ok, err


# Cache the loaded shap-e models across tool calls (avoid re-downloading each call).
_SHAPE_MODELS: dict[str, Any] = {}


def _load_shape_e(kind: str = "text"):
    """Load Shap-E models. kind is 'text' or 'image'."""
    import torch  # type: ignore
    from shap_e.diffusion.sample import sample_latents  # type: ignore
    from shap_e.diffusion.gaussian_diffusion import diffusion_from_config  # type: ignore
    from shap_e.models.download import load_model, load_config  # type: ignore

    device = torch.device("mps" if torch.backends.mps.is_available()
                          else "cuda" if torch.cuda.is_available()
                          else "cpu")

    key = f"{kind}:{device}"
    if key in _SHAPE_MODELS:
        return _SHAPE_MODELS[key]

    # Common: transmitter (latent → mesh)
    xm = load_model("transmitter", device=device)
    # Sampling side
    if kind == "text":
        model = load_model("text300M", device=device)
    else:
        model = load_model("image300M", device=device)
    diffusion = diffusion_from_config(load_config("diffusion"))

    _SHAPE_MODELS[key] = {
        "device": device,
        "xm": xm,
        "model": model,
        "diffusion": diffusion,
        "sample_latents": sample_latents,
    }
    return _SHAPE_MODELS[key]


@tool
def neural_text_to_stl(
    prompt: str,
    output_stl: str,
    guidance_scale: float = 15.0,
    steps: int = 64,
    seed: int = 0,
) -> dict:
    """Generate a 3D mesh from a text prompt using OpenAI Shap-E.

    First call downloads ~1 GB of model weights (cached after).
    Runs on MPS (Apple Silicon), CUDA, or CPU (slow).

    Args:
        prompt: Text description (e.g. "a stylized rocket ship").
        output_stl: Output .stl path.
        guidance_scale: Classifier-free guidance (higher = more prompt-faithful).
        steps: Diffusion steps (32-128 typical; higher = better + slower).
        seed: Random seed for reproducibility.

    Returns:
        {status, content, path, elapsed_sec, device}
    """
    try:
        import torch  # type: ignore
        from shap_e.util.notebooks import decode_latent_mesh  # type: ignore
    except ImportError as e:
        return err(f"shap-e / torch not installed: {e}")

    ctx = _load_shape_e("text")
    device = ctx["device"]
    xm = ctx["xm"]
    model = ctx["model"]
    diffusion = ctx["diffusion"]
    sample_latents = ctx["sample_latents"]

    torch.manual_seed(seed)
    t0 = time.time()
    try:
        latents = sample_latents(
            batch_size=1,
            model=model,
            diffusion=diffusion,
            guidance_scale=guidance_scale,
            model_kwargs=dict(texts=[prompt]),
            progress=True,
            clip_denoised=True,
            use_fp16=False,          # MPS/CPU don't like fp16
            use_karras=True,
            karras_steps=steps,
            sigma_min=1e-3,
            sigma_max=160,
            s_churn=0,
        )
        mesh = decode_latent_mesh(xm, latents[0]).tri_mesh()
    except Exception as e:
        return err(f"generation failed: {type(e).__name__}: {e}")

    out = Path(output_stl).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(out, "wb") as f:
            mesh.write_ply(f)
        # ply → stl via trimesh
        import trimesh  # type: ignore
        m = trimesh.load(str(out), force="mesh")
        m.export(out)  # overwrite with STL if extension is .stl
    except Exception as e:
        # write as ply if stl fails
        alt = out.with_suffix(".ply")
        with open(alt, "wb") as f:
            mesh.write_ply(f)
        return ok(f"generated as PLY (STL failed: {e}) → {alt}", path=str(alt))

    elapsed = time.time() - t0
    size_kb = out.stat().st_size / 1024
    return ok(
        f"generated in {elapsed:.1f}s on {device} → {out.name} ({size_kb:.1f} KB)",
        path=str(out), elapsed_sec=elapsed, device=str(device), prompt=prompt,
    )


@tool
def neural_image_to_stl(
    image_path: str,
    output_stl: str,
    guidance_scale: float = 3.0,
    steps: int = 64,
    seed: int = 0,
) -> dict:
    """Generate a 3D mesh from a reference image using Shap-E image-conditioned model.

    First call downloads ~1 GB of image-conditioned weights.

    Args:
        image_path: Path to reference .png / .jpg.
        output_stl: Output .stl path.
        guidance_scale: Classifier-free guidance (3.0 recommended for image).
        steps: Diffusion steps.
        seed: Random seed.
    """
    try:
        import torch  # type: ignore
        from PIL import Image  # type: ignore
        from shap_e.util.notebooks import decode_latent_mesh  # type: ignore
        from shap_e.util.image_util import load_image  # type: ignore
    except ImportError as e:
        return err(f"shap-e / torch / PIL not installed: {e}")

    src = Path(image_path).resolve()
    if not src.exists():
        return err(f"image not found: {src}")

    ctx = _load_shape_e("image")
    device = ctx["device"]
    xm = ctx["xm"]
    model = ctx["model"]
    diffusion = ctx["diffusion"]
    sample_latents = ctx["sample_latents"]

    torch.manual_seed(seed)
    image = load_image(str(src))
    t0 = time.time()
    try:
        latents = sample_latents(
            batch_size=1,
            model=model,
            diffusion=diffusion,
            guidance_scale=guidance_scale,
            model_kwargs=dict(images=[image]),
            progress=True,
            clip_denoised=True,
            use_fp16=False,
            use_karras=True,
            karras_steps=steps,
            sigma_min=1e-3,
            sigma_max=160,
            s_churn=0,
        )
        mesh = decode_latent_mesh(xm, latents[0]).tri_mesh()
    except Exception as e:
        return err(f"generation failed: {type(e).__name__}: {e}")

    out = Path(output_stl).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    ply = out.with_suffix(".ply")
    with open(ply, "wb") as f:
        mesh.write_ply(f)
    try:
        import trimesh  # type: ignore
        m = trimesh.load(str(ply), force="mesh")
        m.export(out)
    except Exception as e:
        return ok(f"generated as PLY (STL export failed: {e}) → {ply}", path=str(ply))
    elapsed = time.time() - t0
    return ok(
        f"generated in {elapsed:.1f}s on {device} → {out.name} ({out.stat().st_size/1024:.1f} KB)",
        path=str(out), elapsed_sec=elapsed, device=str(device),
    )


# ================================================================
# Point cloud → mesh
# ================================================================


@tool
def pointcloud_from_stl(
    stl_file: str,
    output_xyz: str,
    n_points: int = 10_000,
    method: str = "surface",
) -> dict:
    """Sample a point cloud from an STL mesh.

    Useful for testing reconstruction, generating training data, or exporting
    scan-like representations.

    Args:
        stl_file: Input .stl mesh.
        output_xyz: Output .xyz / .ply point cloud path.
        n_points: Number of points to sample.
        method: 'surface' (uniform surface samples) or 'volume' (fill interior).
    """
    try:
        import trimesh  # type: ignore
        import numpy as np  # type: ignore
    except ImportError as e:
        return err(f"trimesh required: {e}")
    src = Path(stl_file).resolve()
    if not src.exists():
        return err(f"file not found: {src}")
    mesh = trimesh.load(src, force="mesh")
    if method == "surface":
        pts, _ = trimesh.sample.sample_surface(mesh, n_points)
    elif method == "volume":
        pts = trimesh.sample.volume_mesh(mesh, n_points)
        if pts.shape[0] < n_points:
            # top up with surface
            extra, _ = trimesh.sample.sample_surface(mesh, n_points - pts.shape[0])
            pts = np.vstack([pts, extra])
    else:
        return err(f"unknown method '{method}'. Use surface|volume.")

    out = Path(output_xyz).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.suffix.lower() == ".ply":
        trimesh.PointCloud(pts).export(out)
    else:
        np.savetxt(out, pts, fmt="%.6f")
    return ok(f"sampled {len(pts)} points → {out.name}",
              path=str(out), points=int(len(pts)))


@tool
def pointcloud_to_stl(
    pointcloud_file: str,
    output_stl: str,
    method: str = "alpha",
    alpha: float = 5.0,
) -> dict:
    """Reconstruct a mesh from a point cloud (alpha-shape / ball-pivot style).

    Uses trimesh's alpha-shape reconstruction (Delaunay-based) or convex hull
    fallback. For serious Poisson reconstruction you'd want open3d/pymeshlab,
    but this covers the common cases without heavy deps.

    Args:
        pointcloud_file: Input .xyz (whitespace) or .ply file.
        output_stl: Output .stl file.
        method: 'alpha' (concave hull) or 'convex' (convex hull only).
        alpha: Alpha value for concave hull (larger = more concave).
    """
    try:
        import trimesh  # type: ignore
        import numpy as np  # type: ignore
    except ImportError as e:
        return err(f"trimesh required: {e}")

    src = Path(pointcloud_file).resolve()
    if not src.exists():
        return err(f"file not found: {src}")

    if src.suffix.lower() == ".ply":
        pc = trimesh.load(src)
        pts = np.array(pc.vertices) if hasattr(pc, "vertices") else np.asarray(pc)
    else:
        pts = np.loadtxt(src)
        if pts.ndim == 1:
            pts = pts.reshape(-1, 3)

    if pts.shape[0] < 4:
        return err(f"need at least 4 points, got {pts.shape[0]}")

    if method == "convex":
        try:
            mesh = trimesh.Trimesh(vertices=pts).convex_hull
        except Exception as e:
            return err(f"convex hull failed: {e}")
    elif method == "alpha":
        # Use trimesh alpha shape (via scipy Delaunay)
        try:
            from scipy.spatial import Delaunay  # type: ignore
            tri = Delaunay(pts)
            # Filter tetrahedra by circumradius (alpha shape)
            tetras = tri.simplices
            # Compute circumradius per tetra
            a = pts[tetras[:, 0]]
            b = pts[tetras[:, 1]]
            c = pts[tetras[:, 2]]
            d = pts[tetras[:, 3]]
            # Circumsphere radius via Cayley-Menger determinant
            # Simpler heuristic: keep tetras with max edge < alpha
            edges = np.linalg.norm(np.stack([b - a, c - a, d - a, c - b, d - b, d - c]), axis=-1)
            keep = edges.max(axis=0) < alpha
            good = tetras[keep]
            # Extract boundary faces
            faces_all = np.vstack([good[:, [0,1,2]], good[:, [0,1,3]],
                                    good[:, [0,2,3]], good[:, [1,2,3]]])
            faces_sorted = np.sort(faces_all, axis=1)
            faces_str = faces_sorted.view([('a', faces_sorted.dtype)] * 3)
            uniq, counts = np.unique(faces_str, return_counts=True)
            boundary = uniq[counts == 1].view(faces_sorted.dtype).reshape(-1, 3)
            mesh = trimesh.Trimesh(vertices=pts, faces=boundary)
        except Exception as e:
            return err(f"alpha shape failed: {e}. Try method='convex'.")
    else:
        return err(f"unknown method '{method}'. Use alpha|convex.")

    out = Path(output_stl).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    mesh.export(out)
    return ok(f"reconstructed {len(mesh.faces)} faces from {pts.shape[0]} points → {out.name}",
              path=str(out), faces=int(len(mesh.faces)), points=int(pts.shape[0]),
              method=method)


@tool
def pointcloud_downsample(
    pointcloud_file: str,
    output_file: str,
    voxel_size: float = 1.0,
) -> dict:
    """Voxel-downsample a point cloud (one representative point per voxel cube).

    Args:
        pointcloud_file: Input .xyz or .ply.
        output_file: Output (same format as input).
        voxel_size: Voxel edge in mm.
    """
    try:
        import trimesh  # type: ignore
        import numpy as np  # type: ignore
    except ImportError as e:
        return err(f"trimesh required: {e}")
    src = Path(pointcloud_file).resolve()
    if not src.exists():
        return err(f"file not found: {src}")
    if src.suffix.lower() == ".ply":
        pc = trimesh.load(src)
        pts = np.array(pc.vertices)
    else:
        pts = np.loadtxt(src)

    if pts.shape[0] == 0:
        return err("empty point cloud")

    # Voxel grid: floor points to voxel indices, keep one per bucket
    idx = np.floor(pts / voxel_size).astype(int)
    _, unique_ix = np.unique(idx, axis=0, return_index=True)
    ds = pts[unique_ix]

    out = Path(output_file).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.suffix.lower() == ".ply":
        trimesh.PointCloud(ds).export(out)
    else:
        np.savetxt(out, ds, fmt="%.6f")
    return ok(f"downsampled {pts.shape[0]} → {ds.shape[0]} points ({100*ds.shape[0]/pts.shape[0]:.1f}%)",
              path=str(out), points_before=int(pts.shape[0]), points_after=int(ds.shape[0]))
