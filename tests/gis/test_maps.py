"""QGIS output filtering: only the exact known GDAL PNG message is hidden; every other ERROR is shown.
Run: python -m pytest tests/gis"""
import logging

from gis.maps import classify_qgis_output, load_maps_config

PNG_MESSAGE = "ERROR 6: The PNG driver does not support update access to existing datasets."


def test_only_the_exact_png_message_is_hidden():
    output = "\n".join([
        PNG_MESSAGE,
        "exported docs/maps/qa_failures.png",
        "",
        "ERROR 6: The PNG driver does not support something else.",
        PNG_MESSAGE + " (extra text)",
        "ERROR 1: Cannot open layer",
        "ERROR Traceback (most recent call last):",
        "Warning 1: Non-conformant content",
    ])
    result = list(classify_qgis_output(output, load_maps_config()["ignored_qgis_messages"]))
    assert result == [
        (None, PNG_MESSAGE),
        (logging.INFO, "exported docs/maps/qa_failures.png"),
        (logging.WARNING, "ERROR 6: The PNG driver does not support something else."),
        (logging.WARNING, PNG_MESSAGE + " (extra text)"),
        (logging.WARNING, "ERROR 1: Cannot open layer"),
        (logging.WARNING, "ERROR Traceback (most recent call last):"),
        (logging.INFO, "Warning 1: Non-conformant content"),
    ]


def test_config_ignores_exactly_one_known_message():
    assert load_maps_config()["ignored_qgis_messages"] == [PNG_MESSAGE]
