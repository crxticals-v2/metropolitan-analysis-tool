"""
Map rendering utilities.

Both functions accept the ERLCGraph instance explicitly so this module
has zero dependency on the bot object and stays easily testable.
"""

import io
import math

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont
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
# ZOOM AND CROP HELPER
# ------------------------------------------------------------------

OUTPUT_MAP_WIDTH = 950
OUTPUT_MAP_HEIGHT = 600
ZOOM_PADDING_FACTOR = 1.3 # How much to expand the bounding box for context
MIN_CROP_DIM = 100 # Minimum dimension in original map units to avoid extreme zooming

def _get_cropped_and_resized_map(base_image: Image.Image, points: List[tuple[float, float]]) -> Image.Image:
    """
    Crops and resizes an image to a target dimension, focusing on a set of points.
    """
    if not points:
        # If no points, return a resized version of the full map
        return base_image.resize((OUTPUT_MAP_WIDTH, OUTPUT_MAP_HEIGHT), Image.LANCZOS)

    min_x = min(p[0] for p in points)
    max_x = max(p[0] for p in points)
    min_y = min(p[1] for p in points)
    max_y = max(p[1] for p in points)

    center_x = (min_x + max_x) / 2
    center_y = (min_y + max_y) / 2

    bbox_width = max_x - min_x
    bbox_height = max_y - min_y

    # Ensure minimum crop size to avoid extreme zooming on single points or very small clusters
    effective_width = max(bbox_width * ZOOM_PADDING_FACTOR, MIN_CROP_DIM)
    effective_height = max(bbox_height * ZOOM_PADDING_FACTOR, MIN_CROP_DIM)

    target_aspect_ratio = OUTPUT_MAP_WIDTH / OUTPUT_MAP_HEIGHT
    current_aspect_ratio = effective_width / effective_height

    # Adjust effective dimensions to match target aspect ratio
    if current_aspect_ratio < target_aspect_ratio:
        # Bounding box is relatively taller, expand width to match target aspect ratio
        target_crop_width = effective_height * target_aspect_ratio
        target_crop_height = effective_height
    else:
        # Bounding box is relatively wider, expand height to match target aspect ratio
        target_crop_width = effective_width
        target_crop_height = effective_width / target_aspect_ratio

    # Calculate crop box coordinates
    img_w, img_h = base_image.size
    
    left = center_x - target_crop_width / 2
    top = center_y - target_crop_height / 2
    right = center_x + target_crop_width / 2
    bottom = center_y + target_crop_height / 2

    # Clamp crop box to image boundaries
    left = max(0, left)
    top = max(0, top)
    right = min(img_w, right)
    bottom = min(img_h, bottom)

    crop_box = (int(left), int(top), int(right), int(bottom))
    
    cropped_image = base_image.crop(crop_box)
    return cropped_image.resize((OUTPUT_MAP_WIDTH, OUTPUT_MAP_HEIGHT), Image.LANCZOS)

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

    # Collect all points involved in the paths for bounding box calculation
    all_path_coords = []
    for path_nodes in paths_to_draw:
        for node_id in path_nodes:
            node_data = erlc_graph.graph.nodes.get(str(node_id)) or erlc_graph.nodes_data.get(str(node_id))
            if node_data and "x" in node_data and "y" in node_data:
                all_path_coords.append((node_data["x"], node_data["y"]))

    # primary = solid red, others = semi-transparent orange
    colors = [
        (255, 0,   0,   255),
        (255, 165, 0,   180),
        (255, 165, 0,   180),
    ]

    for idx, path_nodes in enumerate(paths_to_draw[:3]):
        color      = colors[0] if idx == 0 else colors[1]
        line_width = 8 if idx == 0 else 4

        for i in range(len(path_nodes) - 1):
            a = path_nodes[i]
            b = path_nodes[i + 1]

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

    final_image = _get_cropped_and_resized_map(img, all_path_coords)
    buffer = io.BytesIO()
    final_image.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


# ------------------------------------------------------------------
# HEATMAP OVERLAY
# ------------------------------------------------------------------

def _lerp(a: int, b: int, t: float) -> int:
    return round(a + (b - a) * t)


def _heat_color(value: int) -> tuple[int, int, int, int]:
    """Weather-map ramp: transparent low end, blue low, yellow mid, white hot."""
    if value <= 0:
        return (0, 0, 0, 0)

    t = value / 255
    stops = [
        (0.00, (0, 0, 0, 0)),
        (0.08, (24, 82, 214, 85)),
        (0.42, (20, 168, 245, 145)),
        (0.68, (255, 222, 68, 190)),
        (1.00, (255, 255, 255, 235)),
    ]

    for idx in range(len(stops) - 1):
        left_t, left_color = stops[idx]
        right_t, right_color = stops[idx + 1]
        if t <= right_t:
            local_t = (t - left_t) / (right_t - left_t)
            return tuple(_lerp(left_color[i], right_color[i], local_t) for i in range(4))

    return stops[-1][1]


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

    valid_points = []
    for node_id, count in heatmap_data.items():
        node_info = erlc_graph.nodes_data.get(node_id)
        if not node_info or "x" not in node_info or "y" not in node_info:
            continue
        valid_points.append((node_info, count))

    if not valid_points:
        buffer = io.BytesIO()
        _get_cropped_and_resized_map(img, []).save(buffer, format="PNG") # Pass empty list to get resized full map
        buffer.seek(0)
        return buffer

    max_count = max(count for _, count in valid_points)
    if max_count <= 0:
        buffer = io.BytesIO()
        _get_cropped_and_resized_map(img, []).save(buffer, format="PNG") # Pass empty list to get resized full map
        return buffer

    # Cool the base map down slightly so the heat layer reads like a weather chart.
    base_tint = Image.new("RGBA", img.size, (8, 25, 45, 60))
    img = Image.alpha_composite(img, base_tint)

    density = Image.new("L", img.size, 0)
    max_log = math.log1p(max_count)

    for node_info, count in valid_points:
        x, y = node_info["x"], node_info["y"]
        intensity = math.log1p(max(0, count)) / max_log
        radius = int(170 + 150 * intensity)
        peak = int(95 + 160 * intensity)

        spot = Image.new("L", img.size, 0)
        spot_draw = ImageDraw.Draw(spot)
        for r in range(radius, 0, -18):
            falloff = 1 - (r / radius) ** 1.75
            value = int(peak * falloff)
            if value <= 0:
                continue
            spot_draw.ellipse([x - r, y - r, x + r, y + r], fill=value)
        density = ImageChops.add(density, spot, scale=1.0, offset=0)

    density = density.filter(ImageFilter.GaussianBlur(radius=52))
    density = density.point(lambda p: min(255, int(p * 1.35)))

    palette = [_heat_color(i) for i in range(256)]
    overlay = Image.merge(
        "RGBA",
        (
            density.point([color[0] for color in palette]),
            density.point([color[1] for color in palette]),
            density.point([color[2] for color in palette]),
            density.point([color[3] for color in palette]),
        ),
    )

    combined_image = Image.alpha_composite(img, overlay)

    # Now, crop and resize the combined image
    final_image = _get_cropped_and_resized_map(combined_image, all_heatmap_coords)

    # Draw legend on the final cropped and resized image
    legend = Image.new("RGBA", final_image.size, (0, 0, 0, 0))
    legend_draw = ImageDraw.Draw(legend)
    margin = 10 # Smaller margin for the smaller image
    bar_w = 150 # Smaller bar width
    bar_h = 8   # Smaller bar height
    x0 = margin
    y0 = final_image.height - margin - bar_h - 10 # Adjust y0 for smaller image

    # Draw a background for the legend
    legend_draw.rounded_rectangle(
        [x0 - 5, y0 - 5, x0 + bar_w + 5, y0 + bar_h + 15],
        radius=5,
        fill=(6, 13, 24, 150),
        outline=(255, 255, 255, 55),
        width=1,
    )

    for i in range(bar_w):
        color = _heat_color(round(i / (bar_w - 1) * 255))
        legend_draw.line([x0 + i, y0, x0 + i, y0 + bar_h], fill=color)
    legend_draw.rectangle([x0, y0, x0 + bar_w, y0 + bar_h], outline=(255, 255, 255, 120), width=1)
    
    try:
        font_legend = ImageFont.load_default(size=8)
    except Exception:
        font_legend = ImageFont.load_default()

    legend_draw.text((x0, y0 + bar_h + 2), "LOW", fill=(210, 230, 255, 230), font=font_legend)
    legend_draw.text((x0 + bar_w // 2 - 10, y0 + bar_h + 2), "MID", fill=(255, 232, 120, 235), font=font_legend)
    legend_draw.text((x0 + bar_w - 20, y0 + bar_h + 2), "HIGH", fill=(255, 255, 255, 240), font=font_legend)

    final_image = Image.alpha_composite(final_image, legend)
    buffer   = io.BytesIO()
    final_image.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer
