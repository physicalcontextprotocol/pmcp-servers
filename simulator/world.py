"""
P-MCP Fleet Simulator — World Model
=====================================
Workspace geometry, obstacle maps, and the grid world used by the
warehouse scenario. Coordinate system: Z-up, meters.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class Vec2:
    x: float
    y: float

    def distance(self, other: "Vec2") -> float:
        return math.hypot(self.x - other.x, self.y - other.y)

    def __add__(self, other: "Vec2") -> "Vec2":
        return Vec2(self.x + other.x, self.y + other.y)

    def __sub__(self, other: "Vec2") -> "Vec2":
        return Vec2(self.x - other.x, self.y - other.y)

    def __mul__(self, scalar: float) -> "Vec2":
        return Vec2(self.x * scalar, self.y * scalar)

    def normalized(self) -> "Vec2":
        d = math.hypot(self.x, self.y) or 1.0
        return Vec2(self.x / d, self.y / d)


@dataclass
class AABB:
    """Axis-aligned bounding box for collision checking."""
    min: Vec2
    max: Vec2

    def contains(self, p: Vec2) -> bool:
        return self.min.x <= p.x <= self.max.x and self.min.y <= p.y <= self.max.y

    def overlaps(self, other: "AABB") -> bool:
        return (
            self.min.x < other.max.x
            and self.max.x > other.min.x
            and self.min.y < other.max.y
            and self.max.y > other.min.y
        )

    def center(self) -> Vec2:
        return Vec2((self.min.x + self.max.x) / 2, (self.min.y + self.max.y) / 2)


@dataclass
class Obstacle:
    id: str
    bounds: AABB
    obstacle_type: str = "shelf"  # shelf | wall | pillar | dynamic

    def collides(self, p: Vec2, robot_radius: float = 0.3) -> bool:
        # Expand AABB by robot_radius for collision
        expanded = AABB(
            Vec2(self.bounds.min.x - robot_radius, self.bounds.min.y - robot_radius),
            Vec2(self.bounds.max.x + robot_radius, self.bounds.max.y + robot_radius),
        )
        return expanded.contains(p)


@dataclass
class Waypoint:
    id: str
    position: Vec2
    waypoint_type: str = "generic"  # dock | pickup | dropoff | charging | generic


class WarehouseWorld:
    """
    100 x 60 m warehouse world.
    Grid-based with shelf rows and navigation aisles.
    """

    WIDTH = 100.0
    HEIGHT = 60.0

    def __init__(self) -> None:
        self.obstacles: Dict[str, Obstacle] = {}
        self.waypoints: Dict[str, Waypoint] = {}
        self._build_layout()

    def _build_layout(self) -> None:
        # Perimeter walls
        walls = [
            ("wall_n", AABB(Vec2(0, 59.5), Vec2(100, 60.5))),
            ("wall_s", AABB(Vec2(0, -0.5), Vec2(100, 0.5))),
            ("wall_e", AABB(Vec2(99.5, 0), Vec2(100.5, 60))),
            ("wall_w", AABB(Vec2(-0.5, 0), Vec2(0.5, 60))),
        ]
        for wid, bounds in walls:
            self.obstacles[wid] = Obstacle(wid, bounds, "wall")

        # Shelf rows: 10 rows × 8 bays
        for row in range(10):
            for bay in range(8):
                ox = 5.0 + bay * 11.0
                oy = 5.0 + row * 5.5
                sid = f"shelf_{row}_{bay}"
                self.obstacles[sid] = Obstacle(
                    sid,
                    AABB(Vec2(ox, oy), Vec2(ox + 8.0, oy + 1.2)),
                    "shelf",
                )

        # Charging stations (south wall)
        for i in range(5):
            cx = 10.0 + i * 18.0
            wid = f"charger_{i}"
            self.waypoints[wid] = Waypoint(wid, Vec2(cx, 2.0), "charging")

        # Docking stations (north wall)
        for i in range(4):
            dx = 12.0 + i * 22.0
            wid = f"dock_{i}"
            self.waypoints[wid] = Waypoint(wid, Vec2(dx, 57.0), "dock")

        # Pickup / Dropoff zones
        for i in range(8):
            px = 6.0 + i * 11.0
            self.waypoints[f"pickup_{i}"] = Waypoint(f"pickup_{i}", Vec2(px, 3.5), "pickup")
            self.waypoints[f"dropoff_{i}"] = Waypoint(f"dropoff_{i}", Vec2(px, 55.0), "dropoff")

    def is_free(self, pos: Vec2, robot_radius: float = 0.3) -> bool:
        if not (0 <= pos.x <= self.WIDTH and 0 <= pos.y <= self.HEIGHT):
            return False
        for obs in self.obstacles.values():
            if obs.collides(pos, robot_radius):
                return False
        return True

    def nearest_free(self, pos: Vec2, robot_radius: float = 0.3, step: float = 0.5) -> Vec2:
        """Find nearest free position to pos using radial search."""
        if self.is_free(pos, robot_radius):
            return pos
        for r in range(1, 20):
            for angle_deg in range(0, 360, 15):
                angle = math.radians(angle_deg)
                candidate = Vec2(
                    pos.x + r * step * math.cos(angle),
                    pos.y + r * step * math.sin(angle),
                )
                if self.is_free(candidate, robot_radius):
                    return candidate
        return Vec2(50.0, 30.0)  # fallback to center

    def get_waypoints_by_type(self, wtype: str) -> List[Waypoint]:
        return [w for w in self.waypoints.values() if w.waypoint_type == wtype]
