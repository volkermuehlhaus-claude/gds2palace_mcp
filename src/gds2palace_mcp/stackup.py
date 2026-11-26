"""Stackup tools: list the bundled stackups, summarize a stackup file."""

import os
import xml.etree.ElementTree

from .common import captured_output, read_stackup


def _round(value, digits=4):
    return None if value is None else round(float(value), digits)


def list_stackups():
    from gds2palace import utilities, stackup_reader
    with captured_output():
        stackup_dir = utilities.get_stackup_dir()
    if stackup_dir is None:
        return {"stackup_dir": None, "stackups": [],
                "note": "This gds2palace installation has no bundled stackups (needs gds2palace 0.9.1 or newer)."}
    stackups = []
    for name in sorted(os.listdir(stackup_dir)):
        if name.endswith('.xml'):
            path = os.path.join(stackup_dir, name)
            stackups.append({"name": name, "path": path,
                             "description": stackup_reader.read_file_description(path)})
    return {"stackup_dir": stackup_dir, "stackups": stackups,
            "note": "Details: README resource gds2palace://stackups/README.md, or inspect_stackup."}


def _variables(path, variable_overrides):
    from gds2palace import stackup_reader
    root = xml.etree.ElementTree.parse(path).getroot()
    variables = stackup_reader.variables_list()
    for data in root.iter("Variable"):
        variables.append(stackup_reader.variable(data))
    with captured_output():
        variables.apply_overrides(variable_overrides)
        variables.resolve_all()
    return {name: (_round(value) if isinstance(value, (int, float)) else value)
            for name, value in variables.as_dict().items()}


def _variable_limits(path, variable_overrides):
    """Allowed values and description of each variable that has them (gds2palace 0.13.0 and newer: Min/Max/Step
    or List, Description), e.g. {"total_thickness": {"allowed": "25 .. 750, step 5", "description": "..."}}."""
    from gds2palace import stackup_reader
    if not hasattr(stackup_reader.variables_list, "check_allowed_values"):
        return {}
    root = xml.etree.ElementTree.parse(path).getroot()
    variables = stackup_reader.variables_list()
    for data in root.iter("Variable"):
        variables.append(stackup_reader.variable(data))
    with captured_output():
        variables.apply_overrides(variable_overrides)
        variables.resolve_all()
        variables.check_allowed_values()
    limits = {}
    for var in variables.variables:
        entry = {key: value for key, value in (("allowed", var.allowed_values_text()), ("description", var.description))
                 if value}
        if entry:
            limits[var.name] = entry
    return limits


def _gds_layer(layernum):
    """{"gds_layer": number} and, for a stackup layer with a Datatype (gds2palace 0.13.0), "gds_datatype"."""
    from gds2palace import stackup_reader
    if not hasattr(stackup_reader, "split_layer_id"):
        return {"gds_layer": int(layernum)}
    number, datatype = stackup_reader.split_layer_id(layernum)
    return {"gds_layer": number, **({"gds_datatype": datatype} if datatype is not None else {})}


def _operand(layernum):
    """A derived layer operand: the layer number, or "number/datatype" for an operand with a Datatype."""
    layer = _gds_layer(layernum)
    return layer["gds_layer"] if "gds_datatype" not in layer else f'{layer["gds_layer"]}/{layer["gds_datatype"]}'


def inspect_stackup(stackup_file, variable_overrides=None):
    path, materials_list, dielectrics_list, metals_list = read_stackup(stackup_file, variable_overrides)
    from gds2palace import stackup_reader

    materials = []
    for m in materials_list.materials:
        entry = {"name": m.name, "type": m.type}
        if m.type == "DIELECTRIC":
            entry.update(permittivity=m.eps, loss_tangent=m.tand)
        if m.sigma:
            entry["conductivity_S_per_m"] = m.sigma
        if getattr(m, "Rs", 0):
            entry["sheet_resistance_ohm"] = m.Rs
        if m.thermalcond:
            entry["thermal_conductivity"] = m.thermalcond
        # gds2palace 0.13.0 and newer: lateral (x, y) and vertical (z) values, e.g. "xy 0.9, z 0.3"
        if getattr(m, "thermal_is_directional", None) and m.thermal_is_directional():
            entry["thermal_conductivity_by_direction"] = m.thermal_description()
        materials.append(entry)

    # Priority (gds2palace 0.13.0 and newer): where volumes overlap, the higher one wins; shown when not 0
    dielectrics = [{"name": d.name, "material": d.material, "zmin": _round(d.zmin), "zmax": _round(d.zmax),
                    "thickness": _round(d.thickness),
                    **({"gds_boundary_layer": _gds_layer(d.gdsboundary)["gds_layer"],
                        **({"gds_boundary_datatype": _gds_layer(d.gdsboundary)["gds_datatype"]}
                           if "gds_datatype" in _gds_layer(d.gdsboundary) else {})}
                       if d.gdsboundary is not None else {}),
                    **({"priority": d.priority} if getattr(d, "priority", 0) else {})}
                   for d in dielectrics_list.dielectrics]

    layers = [{"name": m.name, **_gds_layer(m.layernum), "type": m.type.lower(), "material": m.material,
               "zmin": _round(m.zmin), "zmax": _round(m.zmax), "thickness": _round(m.thickness),
               **({"priority": m.priority} if getattr(m, "priority", 0) else {})}
              for m in sorted(metals_list.metals, key=lambda m: (m.zmin, m.zmax))]

    derived = [{"name": d.name, "gds_layer": int(d.layernum), "operation": d.operation,
                "operands": [_operand(o) for o in d.operands], "oversize": d.oversize}
               for d in metals_list.derived_layers.derived_layers]

    used = {_gds_layer(n)["gds_layer"] for n in list(metals_list.getlayernumbers())
            + list(dielectrics_list.get_boundary_layers())
            + [o for d in metals_list.derived_layers.derived_layers for o in d.operands]}
    free_port_layers = [n for n in range(201, 250) if n not in used][:10]

    return {
        "path": path,
        "description": stackup_reader.read_file_description(path),
        "variables": _variables(path, variable_overrides),
        **({"variable_limits": limits} if (limits := _variable_limits(path, variable_overrides)) else {}),
        "materials": materials,
        "dielectrics": dielectrics,
        "layers_bottom_to_top": layers,
        "derived_layers": derived,
        "free_port_layers": free_port_layers,
        "notes": [
            "z values and thicknesses are in model units (settings['unit'], microns for the IHP stackups), resolved with the given variable overrides.",
            "Port and thermal layers must use GDS layer numbers that the stackup does not use; "
            "free_port_layers lists the first free ones from the conventional range 201-249.",
            "gds2palace stackups and gds2openEMS stackups are not interchangeable.",
        ],
    }
