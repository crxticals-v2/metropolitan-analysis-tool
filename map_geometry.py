import math
import re
from typing import Any, Mapping


DEFAULT_CURVE_STEPS = 24
_RAD_RE = re.compile(r"(?:^|,)\s*rad\s*=\s*([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)")


def parse_connectionstyle_rad(connectionstyle: Any) -> float:
    """Return the arc3 rad value from a NetworkX/Matplotlib connectionstyle."""
    if not connectionstyle:
        return 0.0
    match = _RAD_RE.search(str(connectionstyle))
    if not match:
        return 0.0
    try:
        return float(match.group(1))
    except ValueError:
        return 0.0


def edge_curve_rad(edge: Mapping[str, Any] | None) -> float:
    """Read curve radius from supported map edge shapes."""
    if not edge:
        return 0.0

    curve = edge.get("curve")
    if isinstance(curve, Mapping):
        for key in ("rad", "radius"):
            if curve.get(key) is not None:
                try:
                    return float(curve[key])
                except (TypeError, ValueError):
                    return 0.0

    for key in ("rad", "radius"):
        if edge.get(key) is not None:
            try:
                return float(edge[key])
            except (TypeError, ValueError):
                return 0.0

    return parse_connectionstyle_rad(edge.get("connectionstyle"))


def format_connectionstyle(rad: float) -> str:
    """Format a Matplotlib/NetworkX arc3 connectionstyle."""
    return f"arc3,rad={rad:g}"


def apply_curve_to_edge(edge: dict[str, Any], rad: float) -> None:
    """Persist a curve radius on a JSON edge without changing unrelated fields."""
    if abs(rad) < 1e-9:
        edge.pop("curve", None)
        edge.pop("connectionstyle", None)
        edge.pop("rad", None)
        edge.pop("radius", None)
        return

    edge["connectionstyle"] = format_connectionstyle(rad)
    edge["curve"] = {"style": "arc3", "rad": rad}
    edge.pop("rad", None)
    edge.pop("radius", None)


def edge_connectionstyle(edge: Mapping[str, Any] | None) -> str:
    rad = edge_curve_rad(edge)
    return format_connectionstyle(rad) if abs(rad) >= 1e-9 else "arc3,rad=0"


def curved_edge_points(
    start: tuple[float, float],
    end: tuple[float, float],
    rad: float = 0.0,
    steps: int = DEFAULT_CURVE_STEPS,
) -> list[tuple[float, float]]:
    """
    Sample a NetworkX-style arc3 curve as a quadratic Bezier polyline.

    NetworkX forwards connectionstyle to Matplotlib. For PIL and Tkinter we
    approximate the same intent with a control point offset perpendicular to
    the chord by rad * chord_length.
    """
    x0, y0 = start
    x1, y1 = end
    if abs(rad) < 1e-9:
        return [(x0, y0), (x1, y1)]

    dx = x1 - x0
    dy = y1 - y0
    if dx == 0 and dy == 0:
        return [(x0, y0), (x1, y1)]

    cx = (x0 + x1) / 2 + rad * dy
    cy = (y0 + y1) / 2 - rad * dx
    count = max(2, int(steps))
    points = []
    for idx in range(count + 1):
        t = idx / count
        mt = 1 - t
        x = mt * mt * x0 + 2 * mt * t * cx + t * t * x1
        y = mt * mt * y0 + 2 * mt * t * cy + t * t * y1
        points.append((x, y))
    return points


def edge_points_from_nodes(
    nodes: Mapping[str, Mapping[str, Any]],
    source: str,
    target: str,
    edge: Mapping[str, Any] | None = None,
    steps: int = DEFAULT_CURVE_STEPS,
) -> list[tuple[float, float]]:
    n1 = nodes[source]
    n2 = nodes[target]
    return curved_edge_points(
        (float(n1["x"]), float(n1["y"])),
        (float(n2["x"]), float(n2["y"])),
        edge_curve_rad(edge),
        steps=steps,
    )


def polyline_length(points: list[tuple[float, float]]) -> float:
    if len(points) < 2:
        return 0.0
    return sum(
        math.hypot(x2 - x1, y2 - y1)
        for (x1, y1), (x2, y2) in zip(points, points[1:])
    )
