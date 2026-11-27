"""A stackup whose layers name their GDS datatype (stackup format 3.2): the line is on 68/20, its ground on 69/20,
and both layer numbers also carry shapes on other datatypes that the model does not read. The layout tools must
look at the datatypes the stackup names: the same answers as for a stackup on datatype 0."""

import gdstk
import pytest

from gds2palace_mcp import complexity, holes, layout, meshsize, patterns, ports

STACKUP_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Stackup schemaVersion="3.2">
  <Materials>
    <Material Name="Copper" Type="Conductor" Conductivity="5.8e7" Color="ff8000"/>
    <Material Name="Oxide" Type="Dielectric" Permittivity="4.1" Color="01e0ff"/>
  </Materials>
  <ELayers LengthUnit="um">
    <Dielectrics>
      <Dielectric Name="Oxide" Material="Oxide" Thickness="20" />
    </Dielectrics>
    <Layers>
      <Substrate Offset="0"/>
      <Layer Name="Ground" Type="conductor" Zmin="1" Zmax="2" Material="Copper" Layer="69" Datatype="20" />
      <Layer Name="Via" Type="via" Zmin="2" Zmax="8" Material="Copper" Layer="68" Datatype="44" />
      <Layer Name="Line" Type="conductor" Zmin="8" Zmax="10" Material="Copper" Layer="68" Datatype="20" />
    </Layers>
  </ELayers>
</Stackup>
"""


@pytest.fixture
def files(tmp_path):
    stackup = tmp_path / "datatype_stackup.xml"
    stackup.write_text(STACKUP_XML, encoding="utf-8")
    cell = gdstk.Cell("LINE")
    cell.add(gdstk.rectangle((-80, -110), (880, 110), layer=69, datatype=20))        # ground plane
    cell.add(gdstk.rectangle((0, -7.5), (800, 7.5), layer=68, datatype=20))          # line
    for i in range(30):                                                              # a via field under the line end
        cell.add(gdstk.rectangle((700 + 2 * i, -0.5), (701 + 2 * i, 0.5), layer=68, datatype=44))
    cell.add(gdstk.rectangle((-80, -110), (880, 110), layer=68, datatype=5))         # keepout: not in the stackup
    cell.add(gdstk.rectangle((0, 0), (0, 0.001), layer=201))                         # replaced below by port lines
    cell.filter([(201, 0)], remove=True)
    cell.add(gdstk.rectangle((0, -7.5), (0, 7.5), layer=201), gdstk.rectangle((800, -7.5), (800, 7.5), layer=202))
    library = gdstk.Library()
    library.add(cell)
    gds = tmp_path / "datatype_line.gds"
    library.write_gds(str(gds))
    return str(gds), str(stackup)


def via_port(number, layer):
    return {"portnumber": number, "voltage": 1, "port_Z0": 50, "source_layernum": layer,
            "from_layername": "Ground", "to_layername": "Line", "direction": "z"}


def test_inspect_gds_names_the_roles_per_datatype(files):
    gds, stackup = files
    by_spec = {(e["layer"], e["datatype"]): e["stackup_role"]
               for e in layout.inspect_gds(gds, stackup_file=stackup)["layers"]}
    assert by_spec[(68, 20)] == ["conductor Line"] and by_spec[(68, 44)] == ["via Via"]
    assert by_spec[(69, 20)] == ["conductor Ground"]
    assert by_spec[(68, 5)] == ["not read: the stackup reads this layer number from 68/20, 68/44"]


def test_ports_are_checked_against_the_named_datatypes(files):
    gds, stackup = files
    r = ports.check_ports(gds, stackup, [via_port(1, 201), via_port(2, 202)])
    assert r["ok"] and r["errors"] == [], r
    for p in r["ports"]:
        assert p["coverage"] == {"Ground": 1.0, "Line": 1.0}


def test_mesh_size_sees_the_line(files):
    gds, stackup = files
    r = meshsize.suggest_mesh_size(gds, stackup)
    assert [(t["layer"], t["name"], t["width_um"]) for t in r["traces"]] == [("68/20", "Line", 15.0)]
    assert "10006800020" not in str(r)


def test_complexity_counts_the_named_datatypes(files):
    gds, stackup = files
    r = complexity.check_layout_complexity(gds, stackup)
    counted = {(e["layer"], e.get("datatype")): e["polygons"] for e in r["layers"]}
    assert counted == {(68, 44): 30, (68, 20): 1, (69, 20): 1}
    assert r["polygons_read_by_gds2palace"] == 32
    assert r["port_layer_polygons"] == {201: 1, 202: 1}


def test_repeated_patterns_find_the_via_field(files):
    gds, stackup = files
    r = patterns.check_repeated_patterns(gds, stackup, mincount=20)
    found = [(c["layer"], c["datatype"], c["name"], c["count"], c["read_by_model"]) for c in r["preprocessing_candidates"]]
    assert found == [(68, 44, "Via", 30, True)]


def test_simplify_merges_the_via_field_only(files, tmp_path):
    gds, stackup = files
    out = str(tmp_path / "simplified.gds")
    r = complexity.simplify_layout(gds, stackup, out, merge_vias=True, via_merge_size=1.5)
    cell = gdstk.read_gds(out).top_level()[0]
    counts = {}
    for polygon in cell.polygons:
        counts[(polygon.layer, polygon.datatype)] = counts.get((polygon.layer, polygon.datatype), 0) + 1
    assert counts[(68, 44)] == 1 and counts[(68, 20)] == 1 and counts[(68, 5)] == 1
    assert r["via_layers_merged"] == [(68, 44)] or r["via_layers_merged"] == [[68, 44]]


def test_holes_report_uses_the_stackup_names(files):
    gds, stackup = files
    r = holes.measure_holes(gds, stackup)
    assert all(entry["name"] in ("Line", "Ground") for entry in r["layers"])
