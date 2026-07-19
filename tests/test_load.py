"""Load-test: import all tools, verify @tool decoration, check registry names."""
import strands_cad


def test_all_tools_importable():
    # Core groups always load; optional groups (sim/neural/sdf/dashboard) load
    # when their extras are installed. Assert a sensible floor + that the count
    # matches whatever actually imported (no phantom entries).
    n = len(strands_cad.ALL_TOOLS)
    assert n >= 40, f"expected at least 40 core tools, got {n}"
    assert n == len(strands_cad.__all__) - 2, "ALL_TOOLS should match exported tool names"


def test_each_tool_has_name():
    for t in strands_cad.ALL_TOOLS:
        # Strands @tool objects have a `tool_name` or `__name__` attribute.
        name = getattr(t, "tool_name", None) or getattr(t, "__name__", None)
        assert name, f"tool {t} has no name"


def test_tool_names_unique():
    names = []
    for t in strands_cad.ALL_TOOLS:
        n = getattr(t, "tool_name", None) or getattr(t, "__name__", None)
        names.append(n)
    assert len(names) == len(set(names)), f"duplicate names: {names}"


def test_tool_layer_coverage():
    names = {getattr(t, "tool_name", None) or getattr(t, "__name__", "") for t in strands_cad.ALL_TOOLS}
    expected_prefixes = ["scad_", "gcode_", "stl_", "mf3_", "slice_", "bambu_", "sim_", "preview_", "bom_", "journal_"]  # dashboard_ optional
    for prefix in expected_prefixes:
        assert any(n.startswith(prefix) for n in names), f"no tools with prefix '{prefix}'"


if __name__ == "__main__":
    test_all_tools_importable()
    test_each_tool_has_name()
    test_tool_names_unique()
    test_tool_layer_coverage()
    print(f"✅ all tests passed. {len(strands_cad.ALL_TOOLS)} tools loaded.")
