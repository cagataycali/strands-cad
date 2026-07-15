"""3MF layer — package meshes into 3MF for slicers (Bambu Studio ready)."""
from __future__ import annotations
import zipfile
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

from strands import tool
from strands_cad._common import ok, err, parse_stl


NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"

_CONTENT_TYPES = b'''<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>
</Types>'''

_RELS = b'''<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rel-1" Target="/3D/3dmodel.model" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>
</Relationships>'''


@tool
def mf3_pack(
    items: list[dict],
    output_3mf: str,
    title: str = "strands-cad plate",
) -> dict:
    """Pack one or more STLs into a single .3mf file.

    Args:
        items: List of {stl: <path>, name: <str>, position: [x,y,z]} entries.
            Position is applied as a translation on the build plate.
        output_3mf: Output .3mf path.
        title: 3MF title metadata.

    Returns:
        {status, content, path, size_kb, objects}
    """
    if not items:
        return err("items list is empty")
    out = Path(output_3mf).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    ET.register_namespace("", NS)
    model = ET.Element(f"{{{NS}}}model", unit="millimeter")
    meta = ET.SubElement(model, f"{{{NS}}}metadata", name="Title")
    meta.text = title
    resources = ET.SubElement(model, f"{{{NS}}}resources")
    build = ET.SubElement(model, f"{{{NS}}}build")

    packed = []
    for i, it in enumerate(items, start=1):
        stl_path = it.get("stl")
        name = it.get("name", f"object_{i}")
        pos = it.get("position", [0.0, 0.0, 0.0])
        if not stl_path or not Path(stl_path).exists():
            return err(f"stl not found for item {i}: {stl_path}")
        try:
            verts, tris = parse_stl(stl_path)
        except Exception as e:
            return err(f"failed parsing {stl_path}: {e}")
        obj = ET.SubElement(resources, f"{{{NS}}}object", id=str(i), type="model", name=name)
        mesh = ET.SubElement(obj, f"{{{NS}}}mesh")
        v_el = ET.SubElement(mesh, f"{{{NS}}}vertices")
        for x, y, z in verts:
            ET.SubElement(v_el, f"{{{NS}}}vertex", x=f"{x}", y=f"{y}", z=f"{z}")
        t_el = ET.SubElement(mesh, f"{{{NS}}}triangles")
        for a, b, c in tris:
            ET.SubElement(t_el, f"{{{NS}}}triangle", v1=f"{a}", v2=f"{b}", v3=f"{c}")
        dx, dy, dz = pos
        transform = f"1 0 0 0 1 0 0 0 1 {dx} {dy} {dz}"
        ET.SubElement(build, f"{{{NS}}}item", objectid=str(i), transform=transform)
        packed.append({"name": name, "vertices": len(verts), "triangles": len(tris), "position": pos})

    xml_bytes = b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(model)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CONTENT_TYPES)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("3D/3dmodel.model", xml_bytes)

    size_kb = out.stat().st_size / 1024
    return ok(f"packed {len(packed)} object(s) → {out} ({size_kb:.1f} KB)",
              path=str(out), size_kb=size_kb, objects=packed)


@tool
def mf3_unpack(mf3_file: str, output_dir: str) -> dict:
    """Unpack a .3mf archive to a directory.

    Args:
        mf3_file: Input .3mf path.
        output_dir: Directory to extract into.
    """
    src = Path(mf3_file).resolve()
    dst = Path(output_dir).resolve()
    if not src.exists():
        return err(f"3mf not found: {src}")
    dst.mkdir(parents=True, exist_ok=True)
    files: list[str] = []
    with zipfile.ZipFile(src, "r") as z:
        z.extractall(dst)
        files = z.namelist()
    return ok(f"unpacked {len(files)} entries → {dst}", path=str(dst), files=files)


@tool
def mf3_read_metadata(mf3_file: str) -> dict:
    """Read metadata + object listing from a 3MF without loading geometry.

    Args:
        mf3_file: Path to .3mf file.

    Returns:
        {status, content, title, unit, objects:[{id, name, vertices, triangles}]}
    """
    src = Path(mf3_file).resolve()
    if not src.exists():
        return err(f"3mf not found: {src}")
    try:
        with zipfile.ZipFile(src, "r") as z:
            if "3D/3dmodel.model" not in z.namelist():
                return err("3mf missing 3D/3dmodel.model")
            xml = z.read("3D/3dmodel.model").decode("utf-8")
    except zipfile.BadZipFile:
        return err("not a valid 3mf/zip file")
    root = ET.fromstring(xml)
    ns = {"m": NS}
    title = ""
    for md in root.findall("m:metadata", ns):
        if md.attrib.get("name") == "Title":
            title = md.text or ""
    unit = root.attrib.get("unit", "millimeter")
    objects = []
    for obj in root.findall(".//m:object", ns):
        oid = obj.attrib.get("id", "?")
        name = obj.attrib.get("name", "")
        verts = obj.findall(".//m:vertex", ns)
        tris = obj.findall(".//m:triangle", ns)
        objects.append({"id": oid, "name": name, "vertices": len(verts), "triangles": len(tris)})
    return ok(f"{title or '(untitled)'} — {len(objects)} object(s), unit={unit}",
              title=title, unit=unit, objects=objects)
