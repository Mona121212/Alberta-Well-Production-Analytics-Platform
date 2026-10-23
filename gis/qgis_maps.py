"""Render the QGIS project and PNG maps from a spec written by gis/maps.py.

Runs inside QGIS's Python (PyQGIS), not the project venv:
    "C:/Program Files/QGIS 3.44.15/bin/python-qgis-ltr.bat" gis/qgis_maps.py gis/output/full/maps/map_spec.json

Each map is an A4 landscape print layout: title and subtitle, map frame, legend, north arrow,
scale bar and a source/CRS note. The layouts are saved in the project, so they can be edited in QGIS.
"""
import json
import math
import os
import sys
import traceback

from qgis.core import (Qgis, QgsApplication, QgsCoordinateReferenceSystem, QgsLayoutExporter, QgsLayoutItemLabel,
                       QgsLayoutItemLegend, QgsLayoutItemMap, QgsLayoutItemPage, QgsLayoutItemPicture,
                       QgsLayoutItemScaleBar, QgsLayoutPoint, QgsLayoutSize, QgsLegendStyle, QgsPrintLayout,
                       QgsProject,
                       QgsSymbolLayerUtils, QgsTextFormat, QgsUnitTypes, QgsVectorLayer)
from qgis.PyQt.QtGui import QColor, QFont
from qgis.PyQt.QtXml import QDomDocument

MM = QgsUnitTypes.LayoutMillimeters


def text_format(size, bold=False, colour="#222222"):
    fmt = QgsTextFormat()
    font = QFont("Arial")
    font.setBold(bold)
    fmt.setFont(font)
    fmt.setSize(size)
    fmt.setColor(QColor(colour))
    return fmt


def add_label(layout, text, x, y, w, h, size, bold=False, colour="#222222"):
    label = QgsLayoutItemLabel(layout)
    label.setText(text)
    label.setTextFormat(text_format(size, bold, colour))
    layout.addLayoutItem(label)
    label.attemptMove(QgsLayoutPoint(x, y, MM))
    label.attemptResize(QgsLayoutSize(w, h, MM))
    return label


def nice_segment(km):
    """Round a segment length to 1, 2 or 5 x 10^n km."""
    exponent = math.floor(math.log10(km))
    base = km / 10 ** exponent
    step = 1 if base < 2 else 2 if base < 5 else 5
    return step * 10 ** exponent


def load_layers(project, spec):
    layers = {}
    options = QgsVectorLayer.LayerOptions()
    options.loadDefaultStyle = False  # package GeoPackages embed their own default style; maps use map styles
    for key, item in spec["layers"].items():
        layer = QgsVectorLayer(f"{item['path']}|layername={item['layer']}", item["name"], "ogr", options)
        if not layer.isValid():
            raise RuntimeError(f"Layer {key} is not valid: {item['path']} {item['layer']}")
        # importNamedStyle, not loadNamedStyle: the latter prefers a style stored in the GeoPackage itself.
        doc = QDomDocument()
        with open(item["qml"], encoding="utf-8") as fh:
            doc.setContent(fh.read())
        ok, message = layer.importNamedStyle(doc)
        if not ok:
            raise RuntimeError(f"Style for {key} did not load: {message}")
        project.addMapLayer(layer, True)
        layers[key] = layer
    return layers


def build_layout(project, spec, m, layers):
    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName(m["title"])
    layout.pageCollection().page(0).setPageSize("A4", QgsLayoutItemPage.Landscape)

    add_label(layout, m["title"], 10, 6, 277, 10, 18, bold=True)
    add_label(layout, m["subtitle"], 10, 16, 277, 7, 10, colour="#555555")

    map_layers = [layers[k] for k in reversed(m["layers"])]  # spec lists bottom -> top
    item = QgsLayoutItemMap(layout)
    item.setCrs(QgsCoordinateReferenceSystem(spec["crs"]))
    layout.addLayoutItem(item)
    item.attemptMove(QgsLayoutPoint(10, 26, MM))
    item.attemptResize(QgsLayoutSize(140, 176, MM))
    item.setLayers(map_layers)
    item.setKeepLayerSet(True)
    extent = layers[m["extent"]].extent()
    extent.scale(1.06)
    item.zoomToExtent(extent)
    item.setFrameEnabled(True)
    item.setBackgroundColor(QColor("#fbfbf8"))

    legend = QgsLayoutItemLegend(layout)
    legend.setTitle("Legend")
    legend.setLinkedMap(item)
    legend.setAutoUpdateModel(False)
    root = legend.model().rootGroup()
    root.clear()
    for key in reversed(m["layers"]):
        if spec["layers"][key]["legend"]:
            root.addLayer(layers[key])
    layout.addLayoutItem(legend)
    legend.attemptMove(QgsLayoutPoint(158, 26, MM))
    legend.setSymbolWidth(5)
    legend.setSymbolHeight(3.5)
    legend.rstyle(QgsLegendStyle.Title).setTextFormat(text_format(11, bold=True))
    legend.rstyle(QgsLegendStyle.Group).setTextFormat(text_format(9, bold=True))
    legend.rstyle(QgsLegendStyle.Subgroup).setTextFormat(text_format(9, bold=True))
    legend.rstyle(QgsLegendStyle.SymbolLabel).setTextFormat(text_format(8))
    legend.adjustBoxSize()

    arrow = QgsLayoutItemPicture(layout)
    arrow.setPicturePath(QgsSymbolLayerUtils.svgSymbolNameToPath("arrows/NorthArrow_02.svg", project.pathResolver()))
    layout.addLayoutItem(arrow)
    arrow.attemptMove(QgsLayoutPoint(270, 140, MM))
    arrow.attemptResize(QgsLayoutSize(12, 16, MM))

    bar = QgsLayoutItemScaleBar(layout)
    bar.setStyle("Single Box")
    bar.setLinkedMap(item)
    bar.setUnits(Qgis.DistanceUnit.Kilometers)
    bar.setUnitLabel("km")
    bar.setNumberOfSegments(4)
    bar.setNumberOfSegmentsLeft(0)
    map_km = item.extent().width() / 1000
    bar.setUnitsPerSegment(nice_segment(map_km * (60 / 140) / 4))  # about 60 mm long
    bar.setTextFormat(text_format(8))
    layout.addLayoutItem(bar)
    bar.attemptMove(QgsLayoutPoint(158, 160, MM))

    add_label(layout, spec["note"], 158, 176, 129, 26, 7, colour="#444444")
    return layout


def main(spec_path):
    with open(spec_path, encoding="utf-8") as fh:
        spec = json.load(fh)
    QgsApplication.setPrefixPath(spec["prefix"], True)
    app = QgsApplication([], False)
    app.initQgis()
    project = QgsProject.instance()
    project.setCrs(QgsCoordinateReferenceSystem(spec["crs"]))
    project.setTitle("Alberta wells - GIS track")
    layers = load_layers(project, spec)
    for m in spec["maps"]:
        layout = build_layout(project, spec, m, layers)
        project.layoutManager().addLayout(layout)
        settings = QgsLayoutExporter.ImageExportSettings()
        settings.dpi = spec["dpi"]
        settings.generateWorldFile = False
        settings.exportMetadata = False
        result = QgsLayoutExporter(layout).exportToImage(m["png"], settings)
        if result != QgsLayoutExporter.Success:
            raise RuntimeError(f"Export of {m['name']} failed with code {result}")
        print(f"exported {m['png']}", flush=True)
    if not project.write(spec["project"]):
        raise RuntimeError(f"Could not write project {spec['project']}")
    print(f"project {spec['project']}", flush=True)


if __name__ == "__main__":
    code = 0
    try:
        main(sys.argv[1])
    except Exception:  # report and exit non-zero; QGIS teardown can crash, so exit directly
        print("ERROR " + traceback.format_exc(), flush=True)
        code = 1
    os._exit(code)
