# B-rep CAD — CadQuery

The engineering path. **B-rep / NURBS** solid modeling where fillets, chamfers,
hole patterns, and lossless STEP export are first-class.

!!! success "Ships in core"
    CadQuery is a core dependency — no extra install needed.

## Tools

| Tool | Purpose |
|---|---|
| `cq_render_stl` | CadQuery script → STL |
| `cq_render_step` | Same script → lossless STEP (for Fusion/SolidWorks/CNC) |
| `cq_import_step` | Vendor STEP → STL for slicing |
| `cq_render_svg` | Drawing projection → SVG (great for docs) |

Inside a script, `cq` (cadquery) and `result` (the final Workplane) are the
convention.

## Examples

=== "Calibration cube"

    ```python
    cq_render_stl(script='''
    result = cq.Workplane("XY").box(20,20,20).edges().chamfer(0.8)
    ''', output_stl="cube.stl")
    ```
    *2.2 KB STL in 0.01s.*

=== "Mounting bracket"

    ```python
    cq_render_stl(script='''
    result = (
        cq.Workplane("XY").box(60, 40, 6)
        .edges("|Z").fillet(6)
        .faces(">Z").workplane()
        .rect(46, 26, forConstruction=True).vertices().hole(4.2)  # M4 × 4
        .faces(">Z").workplane().hole(20)                         # center bore
    )
    ''', output_stl="bracket.stl")
    ```
    *150 KB STL, 0% overhangs, fits X1C bed.*

=== "T-block (Push-T)"

    ```python
    cq_render_stl(script='''
    result = (
        cq.Workplane("XY").box(80, 20, 15)
        .union(cq.Workplane("XY").center(0, -30).box(20, 40, 15))
        .edges("|Z").fillet(2)
    )
    ''', output_stl="t_block.stl")
    ```
    *51 KB STL, 18.05 g PLA.*

=== "Peg-in-hole board"

    ```python
    cq_render_stl(script='''
    board = cq.Workplane("XY").box(120, 50, 12).edges("|Z").fillet(4)
    result = (board.faces(">Z").workplane()
        .pushPoints([(-40, 0)]).hole(10.4)
        .pushPoints([(0, 0)]).hole(15.4)
        .pushPoints([(40, 0)]).hole(20.4))
    ''', output_stl="peg_board.stl")
    ```
    *100 KB STL — grasping/insertion training.*

## Lossless STEP for downstream CAD/CNC

Same script, engineering-grade output:

```python
cq_render_step(script="result = cq.Workplane('XY').box(30,30,30)",
               output_step="part.step")
```

## Importing STEP

Bring vendor parts into the pipeline:

```python
cq_import_step(input_step="vendor_flange.step", output_stl="flange.stl")
```

## Drawings for docs

```python
cq_render_svg(script="result = cq.Workplane('XY').box(30,30,30)",
              output_svg="drawing.svg")
```

Next: [Implicit Math — SDF →](sdf.md)
