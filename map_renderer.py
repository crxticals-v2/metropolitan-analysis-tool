"""
Map rendering utilities.

Both functions accept the ERLCGraph instance explicitly so this module
has zero dependency on the bot object and stays easily testable.
"""

import io

from PIL import Image, ImageDraw
from typing import List, Dict

from config import MAP_IMAGE_PATH

# --- Global Image Cache ---
_CACHED_BASE_MAP = None

def get_base_map():
    """Lazy-load and cache the base map image in RGBA format."""
    global _CACHED_BASE_MAP
    if _CACHED_BASE_MAP is None:
        try:
            _CACHED_BASE_MAP = Image.open(MAP_IMAGE_PATH).convert("RGBA")
        except Exception as e:
            raise RuntimeError(f"Failed to initialize map cache: {e}")
    return _CACHED_BASE_MAP.copy()

# ------------------------------------------------------------------
# PATH OVERLAY
# ------------------------------------------------------------------

def draw_map_path(erlc_graph, paths_to_draw: List[List[str]]) -> io.BytesIO:
    """
    Draw predicted suspect routes on the ER:LC map image.

    Args:
        erlc_graph:     ERLCGraph instance (provides node coords + edge geometry).
        paths_to_draw:  List of node-id lists (each list = one route to draw).

    Returns:
        PNG image as a BytesIO buffer, or raises RuntimeError on failure.
    """
    try:
        img = get_base_map()
    except Exception as e:
        raise RuntimeError(f"Failed to load base map for path drawing: {e}")
    draw = ImageDraw.Draw(img)
    print("[MAP DEBUG] Drawing map with", len(paths_to_draw), "paths")

    # primary = solid red, others = semi-transparent orange
    colors = [
        (255, 0,   0,   255),
        (255, 165, 0,   180),
        (255, 165, 0,   180),
    ]

    for idx, path_nodes in enumerate(paths_to_draw[:3]):
        print(f"[MAP DEBUG] Drawing path {idx}:", path_nodes)
        color      = colors[0] if idx == 0 else colors[1]
        line_width = 8 if idx == 0 else 4

        for i in range(len(path_nodes) - 1):
            a = path_nodes[i]
            b = path_nodes[i + 1]
            print(f"[MAP DEBUG] Segment: {a} -> {b}")

            edge_data = erlc_graph.graph.get_edge_data(a, b)
            if edge_data:
                geometry = edge_data.get("geometry")
                if geometry and len(geometry) >= 2:
                    draw.line(geometry, fill=color, width=line_width)
                    continue

            # fallback: straight line between node coordinates
            node_a = erlc_graph.graph.nodes.get(str(a))
            node_b = erlc_graph.graph.nodes.get(str(b))

            if not node_a or not node_b:
                print(f"[MAP WARN] Missing node data: {a} -> {b}")
                continue
            if node_a.get("x") is None or node_b.get("x") is None:
                continue

            draw.line(
                [(node_a["x"], node_a["y"]), (node_b["x"], node_b["y"])],
                fill=color,
                width=line_width,
            )

    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


# ------------------------------------------------------------------
# HEATMAP OVERLAY
# ------------------------------------------------------------------

def draw_heatmap_overlay(erlc_graph, heatmap_data: Dict[str, int]) -> io.BytesIO:
    """
    Draw a crime-frequency heatmap on the ER:LC map image.

    Args:
        erlc_graph:    ERLCGraph instance (provides node coords).
        heatmap_data:  {node_id: count} mapping built from MongoDB aggregation.

    Returns:
        PNG image as a BytesIO buffer, or raises RuntimeError on failure.
    """
    try:
        img = get_base_map()
    except Exception as e:
        raise RuntimeError(f"Failed to load base map for heatmap: {e}")

    if not heatmap_data:
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        buffer.seek(0)
        return buffer

    overlay    = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw       = ImageDraw.Draw(overlay)
    max_count  = max(heatmap_data.values())
    base_radius = 45

    for node_id, count in heatmap_data.items():
        node_info = erlc_graph.nodes_data.get(node_id)
        if not node_info or "x" not in node_info or "y" not in node_info:
            continue

        x, y      = node_info["x"], node_info["y"]
        intensity = count / max_count

        for r in range(base_radius, 0, -3):
            alpha = int(140 * intensity * (1 - (r / base_radius) ** 1.5))
            draw.ellipse([x - r, y - r, x + r, y + r], fill=(255, 0, 0, alpha))

    combined = Image.alpha_composite(img, overlay)
    buffer   = io.BytesIO()
    combined.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer
