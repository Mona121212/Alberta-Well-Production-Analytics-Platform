"""QGIS layer styles (.qml) written as plain XML, so packages do not need QGIS installed.

Colours live in config/gis_styles.yaml; the same palette is used by the maps (gis/maps.py).
"""
from xml.sax.saxutils import quoteattr

import yaml

from gis.common import REPO_ROOT

DEFAULT_STYLE_CONFIG = REPO_ROOT / "config" / "gis_styles.yaml"
HEADER = "<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>\n"


def load_style_config(path=DEFAULT_STYLE_CONFIG):
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def rgba(hex_colour, alpha=255):
    h = hex_colour.lstrip("#")
    return f"{int(h[0:2], 16)},{int(h[2:4], 16)},{int(h[4:6], 16)},{alpha}"


def option(name, value):
    return f'<Option name={quoteattr(name)} type="QString" value={quoteattr(str(value))}/>'


def marker_symbol(index, colour, size, outline="#333333", shape="circle"):
    opts = "".join(option(k, v) for k, v in [
        ("color", rgba(colour)), ("name", shape), ("size", size), ("size_unit", "MM"),
        ("outline_color", rgba(outline)), ("outline_width", 0.15), ("outline_width_unit", "MM"),
        ("outline_style", "solid")])
    return (f'<symbol type="marker" name="{index}" alpha="1" clip_to_extent="1" force_rhr="0">'
            f'<layer class="SimpleMarker" enabled="1" pass="0" locked="0"><Option type="Map">{opts}</Option></layer>'
            f'</symbol>')


def fill_symbol(index, colour, outline, outline_width=0.4, fill_alpha=255, no_fill=False):
    opts = "".join(option(k, v) for k, v in [
        ("color", rgba(colour, fill_alpha)), ("style", "no" if no_fill else "solid"),
        ("outline_color", rgba(outline)), ("outline_width", outline_width), ("outline_width_unit", "MM"),
        ("outline_style", "solid"), ("joinstyle", "miter")])
    return (f'<symbol type="fill" name="{index}" alpha="1" clip_to_extent="1" force_rhr="0">'
            f'<layer class="SimpleFill" enabled="1" pass="0" locked="0"><Option type="Map">{opts}</Option></layer>'
            f'</symbol>')


def line_symbol(index, colour, width=0.3):
    opts = "".join(option(k, v) for k, v in [
        ("line_color", rgba(colour)), ("line_width", width), ("line_width_unit", "MM"), ("line_style", "solid")])
    return (f'<symbol type="line" name="{index}" alpha="1" clip_to_extent="1" force_rhr="0">'
            f'<layer class="SimpleLine" enabled="1" pass="0" locked="0"><Option type="Map">{opts}</Option></layer>'
            f'</symbol>')


def document(renderer, labels=""):
    return (HEADER + '<qgis version="3.44.0" styleCategories="Symbology|Labeling">'
            f"{renderer}{labels}</qgis>\n")


def categorized(field, categories, geometry="point", size=1.6, default_colour="#000000"):
    """categories: list of (value, label, colour)."""
    cats, symbols = [], []
    for i, (value, label, colour) in enumerate(categories):
        cats.append(f'<category value={quoteattr(str(value))} symbol="{i}" label={quoteattr(label)} render="true"/>')
        symbols.append(symbol_for(geometry, i, colour, size))
    other = len(categories)
    cats.append(f'<category value="" symbol="{other}" label="other" render="true"/>')
    symbols.append(symbol_for(geometry, other, default_colour, size))
    renderer = (f'<renderer-v2 type="categorizedSymbol" attr={quoteattr(field)} symbollevels="0" '
                f'enableorderby="0" forceraster="0"><categories>{"".join(cats)}</categories>'
                f'<symbols>{"".join(symbols)}</symbols></renderer-v2>')
    return document(renderer)


def single(geometry, colour, size=1.6, outline="#333333", no_fill=False, outline_width=0.4):
    if geometry == "polygon":
        sym = fill_symbol(0, colour, outline, outline_width=outline_width, no_fill=no_fill, fill_alpha=90)
    else:
        sym = symbol_for(geometry, 0, colour, size)
    renderer = (f'<renderer-v2 type="singleSymbol" symbollevels="0" enableorderby="0" forceraster="0">'
                f'<symbols>{sym}</symbols></renderer-v2>')
    return document(renderer)


def symbol_for(geometry, index, colour, size):
    if geometry == "point":
        return marker_symbol(index, colour, size)
    if geometry == "line":
        return line_symbol(index, colour)
    return fill_symbol(index, colour, "#444444", outline_width=0.2, fill_alpha=200)


def qml_for_layer(layer_name, geometry, style_cfg):
    """Build the .qml text for a package or project layer from config/gis_styles.yaml."""
    spec = style_cfg["layers"].get(layer_name)
    if spec is None:
        return None
    if spec["type"] == "categorized":
        palette = style_cfg["palettes"][spec["palette"]]
        cats = [(value, item["label"], item["colour"]) for value, item in palette.items()]
        return categorized(spec["field"], cats, geometry, spec.get("size", 1.6))
    return single(geometry, spec["colour"], spec.get("size", 1.6), no_fill=spec.get("no_fill", False),
                  outline=spec.get("outline", "#333333"), outline_width=spec.get("outline_width", 0.4))
