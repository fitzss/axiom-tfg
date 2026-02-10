"""Shared math and I/O helpers."""

from __future__ import annotations

import math


def euclidean_distance(a: list[float], b: list[float]) -> float:
    """Euclidean distance between two 3-D points."""
    return math.sqrt(sum((ai - bi) ** 2 for ai, bi in zip(a, b)))


def project_onto_sphere(
    center: list[float],
    target: list[float],
    radius: float,
) -> list[float]:
    """Project *target* onto the surface of a sphere at *center* with *radius*.

    Returns the closest point on the sphere surface along the line from
    center to target.  If center == target the original target is returned
    unchanged (degenerate case).
    """
    dist = euclidean_distance(center, target)
    if dist == 0.0:
        return list(target)
    scale = radius / dist
    return [c + (t - c) * scale for c, t in zip(center, target)]


def point_toward(
    source: list[float],
    destination: list[float],
    step: float,
) -> list[float]:
    """Move *source* toward *destination* by *step* metres."""
    dist = euclidean_distance(source, destination)
    if dist == 0.0:
        return list(source)
    scale = step / dist
    return [s + (d - s) * scale for s, d in zip(source, destination)]
