# Neural — text / image → 3D

The AI path. **Shap-E** turns a text prompt or a reference photo straight into a
mesh — perfect for concept props and organic shapes no one wants to model by hand.

!!! note "Needs torch + weights"
    ```bash
    pip install "strands-cad[neural]"            # torch/torchvision
    python -m strands_cad.install_extras neural  # openai/shap-e weights
    ```
    First call downloads ~1 GB of weights; runs on **MPS / CUDA / CPU**.

## Tools

| Tool | Input | Output |
|---|---|---|
| `neural_text_to_stl` | text prompt | STL |
| `neural_image_to_stl` | reference photo | STL |

Also in the neural group (point-cloud round-tripping for perception data):

| Tool | Purpose |
|---|---|
| `pointcloud_from_stl` | Sample an STL surface → `.xyz` cloud |
| `pointcloud_to_stl` | Reconstruct a mesh from a cloud |
| `pointcloud_downsample` | Voxel/uniform downsample a cloud |

## Text → 3D

```python
from strands_cad import neural_text_to_stl

neural_text_to_stl(
    prompt="a stylized rocket ship",
    output_stl="rocket.stl",
    steps=64,          # more steps = higher fidelity, slower
)
```

## Image → 3D

```python
from strands_cad import neural_image_to_stl

neural_image_to_stl(image_path="reference.jpg", output_stl="from_photo.stl")
```

## Point-cloud round trip

Handy for generating synthetic scan data for perception training, then closing
the loop back to a mesh:

```python
pointcloud_from_stl(stl_file="t_block.stl", output_xyz="scan.xyz", n_points=5000)
pointcloud_downsample(pointcloud_file="scan.xyz", output_xyz="scan_lite.xyz", voxel=1.0)
pointcloud_to_stl(pointcloud_file="scan.xyz", output_stl="reconstructed.stl")
```

!!! warning "Not dimensionally precise"
    Neural output is great for *shape*, not *engineering tolerances*. For
    functional parts, use [CadQuery](cadquery.md) or [SCAD](scad.md), or clean up
    neural output with the [mesh QA tools](../pipeline/verify.md).

Next: [2D → 3D →](two-to-three.md)
