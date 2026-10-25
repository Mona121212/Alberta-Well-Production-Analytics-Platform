"""Shared spatial helpers: distance clustering (multi-well pads)."""
import numpy as np
import pandas as pd
import shapely
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components


def cluster_points(geometries, distance):
    """Single-linkage clusters: points closer than `distance` (CRS units) share a label.

    Uses an STRtree "dwithin" query for candidate pairs, then connected components,
    so it scales to the whole province without a full distance matrix.
    """
    geoms = np.asarray(geometries, dtype=object)
    tree = shapely.STRtree(geoms)
    left, right = tree.query(geoms, predicate="dwithin", distance=distance)
    n = len(geoms)
    graph = coo_matrix((np.ones(len(left), dtype=bool), (left, right)), shape=(n, n))
    _, labels = connected_components(graph, directed=False)
    return labels


def assign_pads(wells, distance, key="licence_number"):
    """Pad id and size for every well with a geometry. Pad id = smallest licence number in the pad
    (stable across runs as long as that licence exists)."""
    has_geom = wells.geometry.notna() & ~wells.geometry.is_empty
    labels = pd.Series(-1, index=wells.index)
    labels[has_geom] = cluster_points(wells.geometry[has_geom].to_numpy(), distance)
    pad_id = "PAD-" + wells[key].groupby(labels).transform("min")
    pad_size = wells[key].groupby(labels).transform("size")
    return pad_id.where(has_geom), pad_size.where(has_geom)
