#!/usr/bin/env python3
"""
P-MCP Fleet Simulator
===================

A warehouse simulation with 50 robots demonstrating:
- Multi-robot coordination
- Temporal lease system
- Collision avoidance
- Task allocation
- Real-time monitoring

The simulation models:
- A warehouse with zones (receiving, storage, picking, shipping)
- 50 robots of different types (AMR, arm robots, conveyors)
- Task generation and allocation
- Real-time position tracking and collision detection

Usage:
    python examples/warehouse_multi_robot.py
    python examples/warehouse_multi_robot.py --robots 50 --visualize
    python examples/warehouse_multi_robot.py --test
"""

import argparse
import asyncio
import math
import random
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple
import logging

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(name)s] %(message)s'
)
logger = logging.getLogger("pmcp-fleet")


# ============================================================================
# Warehouse Layout
# ============================================================================

@dataclass
class Position:
    """2D position."""
    x: float
    y: float
    
    def distance_to(self, other: 'Position') -> float:
        """Calculate distance to another position."""
        return math.sqrt((self.x - other.x) ** 2 + (self.y - other.y) ** 2)
    
    def to_tuple(self) -> Tuple[float, float]:
        return (self.x, self.y)
    
    def __repr__(self):
        return f"({self.x:.1f}, {self.y:.1f})"


class Zone(Enum):
    """Warehouse zones."""
    RECEIVING = "receiving"
    STORAGE = "storage"
    PICKING = "picking"
    SHIPPING = "shipping"
    CHARGING = "charging"
    CORRIDOR = "corridor"


@dataclass
class WarehouseZone:
    """A defined zone in the warehouse."""
    zone_type: Zone
    bounds: Tuple[float, float, float, float]  # x_min, x_max, y_min, y_max
    
    def contains(self, pos: Position) -> bool:
        """Check if position is within zone."""
        return (self.bounds[0] <= pos.x <= self.bounds[1] and
                self.bounds[2] <= pos.y <= self.bounds[3])
    
    def random_position(self) -> Position:
        """Generate random position within zone."""
        return Position(
            x=random.uniform(self.bounds[0], self.bounds[1]),
            y=random.uniform(self.bounds[2], self.bounds[3])
        )


class WarehouseLayout:
    """Complete warehouse layout."""
    
    def __init__(self, width: float = 100.0, height: float = 60.0):
        self.width = width
        self.height = height
        
        # Define zones
        self.zones: Dict[Zone, WarehouseZone] = {
            Zone.RECEIVING: WarehouseZone(Zone.RECEIVING, (0, 20, 0, 20)),
            Zone.STORAGE: WarehouseZone(Zone.STORAGE, (20, 50, 0, 60)),
            Zone.PICKING: WarehouseZone(Zone.PICKING, (50, 80, 0, 40)),
            Zone.SHIPPING: WarehouseZone(Zone.SHIPPING, (80, 100, 0, 20)),
            Zone.CHARGING: WarehouseZone(Zone.CHARGING, (0, 20, 40, 60)),
            Zone.CORRIDOR: WarehouseZone(Zone.CORRIDOR, (0, 100, 20, 40)),
        }
        
        # Obstacles (static)
        self.obstacles: List[Tuple[float, float, float, float]] = [
            (25, 25, 2, 2),   # Pillar 1
            (35, 35, 2, 2),   # Pillar 2
            (45, 15, 2, 2),   # Pillar 3
            (55, 45, 2, 2),   # Pillar 4
            (75, 25, 2, 2),   # Pillar 5
        ]
        
        logger.info(f"Warehouse layout: {width}x{height}m")
    
    def is_valid_position(self, pos: Position, robot_radius: float = 0.5) -> bool:
        """Check if position is valid (not in obstacle or out of bounds)."""
        # Check bounds
        if not (0 <= pos.x <= self.width and 0 <= pos.y <= self.height):
            return False
        
        # Check obstacles
        for ox, oy, ow, oh in self.obstacles:
            if (ox - robot_radius <= pos.x <= ox + ow + robot_radius and
                oy - robot_radius <= pos.y <= oy + oh + robot_radius):
                return False
        
        return True
    
    def get_zone(self, pos: Position) -> Optional[Zone]:
        """Get zone containing position."""
        for zone_type, zone in self.zones.items():
            if zone.contains(pos):
                return zone_type
        return None
    
    def get_nearest_zone(self, pos: Position, zone_type: Zone) -> Position:
        """Get nearest position in a specific zone."""
        zone = self.zones[zone_type]
        return zone.random_position()


# ============================================================================
# Robot Types and States
# ============================================================================

class RobotType(Enum):
    """Types of robots in the warehouse."""
    AMR = "amr"           # Autonomous Mobile Robot
    PICKER = "picker"     # Picking robot with arm
    CONVEYOR = "conveyor" # Conveyor belt segment
    FORKLIFT = "forklift" # Heavy lifting


class RobotState(Enum):
    """Robot operational state."""
    IDLE = "idle"
    MOVING = "moving"
    WORKING = "working"
    CHARGING = "charging"
    MAINTENANCE = "maintenance"
    ERROR = "error"


class TaskStatus(Enum):
    """Task status."""
    PENDING = "pending"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class Robot:
    """A warehouse robot."""
    robot_id: str
    robot_type: RobotType
    name: str
    position: Position
    target: Optional[Position]
    velocity: float = 0.0
    state: RobotState = RobotState.IDLE
    battery: float = 100.0
    current_task_id: Optional[str] = None
    zone_lease: Optional[str] = None
    last_update: float = field(default_factory=time.time)
    collision_radius: float = 0.5
    max_speed: float = 2.0  # m/s
    tasks_completed: int = 0
    
    @property
    def is_available(self) -> bool:
        """Check if robot is available for new tasks."""
        return (self.state in [RobotState.IDLE, RobotState.WORKING] and
                self.battery > 20.0 and
                self.current_task_id is None)
    
    @property
    def needs_charging(self) -> bool:
        """Check if robot needs charging."""
        return self.battery < 20.0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "robotId": self.robot_id,
            "name": self.name,
            "type": self.robot_type.value,
            "position": self.position.to_tuple(),
            "state": self.state.value,
            "battery": self.battery,
            "currentTask": self.current_task_id,
            "zoneLease": self.zone_lease,
        }


@dataclass
class Task:
    """A warehouse task."""
    task_id: str
    task_type: str  # "pick", "move", "charge", "maintain"
    priority: int
    source_zone: Optional[Zone]
    target_zone: Optional[Zone]
    assigned_robot_id: Optional[str] = None
    status: TaskStatus = TaskStatus.PENDING
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    estimated_duration: float = 60.0
    payload: Dict[str, Any] = field(default_factory=dict)
    
    @property
    def is_active(self) -> bool:
        return self.status in [TaskStatus.ASSIGNED, TaskStatus.IN_PROGRESS]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "taskId": self.task_id,
            "taskType": self.task_type,
            "priority": self.priority,
            "status": self.status.value,
            "assignedRobot": self.assigned_robot_id,
            "createdAt": self.created_at,
        }


# ============================================================================
# Fleet Controller
# ============================================================================

class FleetController:
    """Controls the fleet of robots."""
    
    def __init__(self, layout: WarehouseLayout, num_robots: int = 50):
        self.layout = layout
        self.num_robots = num_robots
        
        # Robots and tasks
        self.robots: Dict[str, Robot] = {}
        self.tasks: Dict[str, Task] = {}
        self.task_queue: List[Task] = []
        
        # Zone leases (temporal coordination)
        self.zone_leases: Dict[str, Dict[str, Any]] = {}
        
        # Metrics
        self.start_time = time.time()
        self.total_tasks = 0
        self.completed_tasks = 0
        self.failed_tasks = 0
        self.collisions = 0
        
        # Initialize robots
        self._initialize_robots()
        
        # Generate initial tasks
        self._generate_initial_tasks()
        
        logger.info(f"Fleet initialized with {num_robots} robots")
    
    def _initialize_robots(self):
        """Create robots of different types."""
        # Distribution: 30 AMR, 10 pickers, 5 conveyors, 5 forklifts
        distributions = [
            (RobotType.AMR, 30),
            (RobotType.PICKER, 10),
            (RobotType.CONVEYOR, 5),
            (RobotType.FORKLIFT, 5),
        ]
        
        robot_id = 0
        
        for robot_type, count in distributions:
            for i in range(count):
                # Create robot
                pos = self._get_valid_spawn_position()
                
                robot = Robot(
                    robot_id=f"R{robot_id:03d}",
                    robot_type=robot_type,
                    name=f"{robot_type.value.upper()}-{robot_id:03d}",
                    position=pos,
                    target=None,
                    collision_radius=self._get_collision_radius(robot_type),
                    max_speed=self._get_max_speed(robot_type),
                )
                
                self.robots[robot.robot_id] = robot
                robot_id += 1
    
    def _get_collision_radius(self, robot_type: RobotType) -> float:
        radii = {
            RobotType.AMR: 0.5,
            RobotType.PICKER: 0.8,
            RobotType.CONVEYOR: 0.3,
            RobotType.FORKLIFT: 1.0,
        }
        return radii.get(robot_type, 0.5)
    
    def _get_max_speed(self, robot_type: RobotType) -> float:
        speeds = {
            RobotType.AMR: 2.0,
            RobotType.PICKER: 1.5,
            RobotType.CONVEYOR: 1.0,
            RobotType.FORKLIFT: 1.2,
        }
        return speeds.get(robot_type, 1.5)
    
    def _get_valid_spawn_position(self) -> Position:
        """Get a valid spawn position."""
        for _ in range(100):
            pos = Position(
                x=random.uniform(5, 95),
                y=random.uniform(5, 55)
            )
            if self.layout.is_valid_position(pos):
                return pos
        
        # Fallback to center
        return Position(50.0, 30.0)
    
    def _generate_initial_tasks(self):
        """Generate initial set of tasks."""
        task_types = ["pick", "move", "charge"]
        
        for i in range(self.num_robots * 2):
            task_type = random.choice(task_types)
            
            # Determine zones
            if task_type == "pick":
                source = Zone.RECEIVING
                target = Zone.PICKING
            elif task_type == "move":
                source = random.choice([Zone.STORAGE, Zone.PICKING])
                target = random.choice([Zone.STORAGE, Zone.SHIPPING])
            else:  # charge
                source = None
                target = Zone.CHARGING
            
            task = Task(
                task_id=f"T{i:05d}",
                task_type=task_type,
                priority=random.randint(1, 10),
                source_zone=source,
                target_zone=target,
            )
            
            self.tasks[task.task_id] = task
            self.task_queue.append(task)
            self.total_tasks += 1
        
        # Sort by priority (higher first)
        self.task_queue.sort(key=lambda t: -t.priority)
        
        logger.info(f"Generated {len(self.task_queue)} initial tasks")
    
    # --- Task Allocation ---
    
    def assign_task(self, task_id: str, robot_id: str) -> bool:
        """Assign a task to a robot."""
        if task_id not in self.tasks or robot_id not in self.robots:
            return False
        
        task = self.tasks[task_id]
        robot = self.robots[robot_id]
        
        if not robot.is_available:
            return False
        
        # Update task
        task.status = TaskStatus.ASSIGNED
        task.assigned_robot_id = robot_id
        
        # Update robot
        robot.current_task_id = task_id
        robot.state = RobotState.WORKING
        
        # Determine target
        if task.target_zone:
            robot.target = self.layout.get_nearest_zone(robot.position, task.target_zone)
        
        return True
    
    def _allocate_tasks(self):
        """Allocate pending tasks to available robots."""
        for task in self.task_queue:
            if task.status != TaskStatus.PENDING:
                continue
            
            # Find best robot
            best_robot = None
            best_distance = float('inf')
            
            for robot in self.robots.values():
                if not robot.is_available:
                    continue
                
                # Calculate distance
                dist = robot.position.distance_to(task.target_zone or Zone.STORAGE)
                
                if dist < best_distance:
                    best_distance = dist
                    best_robot = robot
            
            # Assign if found
            if best_robot and best_distance < 50:  # Max assignment distance
                self.assign_task(task.task_id, best_robot.robot_id)
    
    # --- Simulation Step ---
    
    def update(self, dt: float):
        """Update simulation one step."""
        # Update each robot
        for robot in self.robots.values():
            self._update_robot(robot, dt)
        
        # Check for collisions
        self._check_collisions()
        
        # Reallocate tasks
        self._allocate_tasks()
        
        # Generate new tasks occasionally
        if random.random() < 0.05:  # 5% chance per frame
            self._generate_task()
        
        # Cleanup completed tasks
        self._cleanup_completed_tasks()
    
    def _update_robot(self, robot: Robot, dt: float):
        """Update a single robot."""
        # Battery drain
        if robot.state != RobotState.IDLE:
            robot.battery -= 0.1 * dt
            robot.battery = max(0, robot.battery)
        
        # Check charging need
        if robot.needs_charging and robot.state != RobotState.CHARGING:
            robot.state = RobotState.CHARGING
            robot.target = self.layout.get_nearest_zone(robot.position, Zone.CHARGING)
        
        # Movement
        if robot.target:
            self._move_towards_target(robot, dt)
        else:
            if robot.state == RobotState.MOVING:
                robot.state = RobotState.IDLE
                robot.velocity = 0.0
        
        # Update timestamp
        robot.last_update = time.time()
    
    def _move_towards_target(self, robot: Robot, dt: float):
        """Move robot towards target."""
        # Calculate direction
        dx = robot.target.x - robot.position.x
        dy = robot.target.y - robot.position.y
        distance = math.sqrt(dx * dx + dy * dy)
        
        if distance < 0.1:
            # Reached target
            robot.position = robot.target
            robot.target = None
            robot.state = RobotState.IDLE
            
            # Check if task completed
            if robot.current_task_id:
                task = self.tasks.get(robot.current_task_id)
                if task:
                    task.status = TaskStatus.COMPLETED
                    task.completed_at = time.time()
                    robot.tasks_completed += 1
                    self.completed_tasks += 1
                
                robot.current_task_id = None
                robot.state = RobotState.IDLE
            
            return
        
        # Normalize and apply velocity
        vx = (dx / distance) * robot.max_speed
        vy = (dy / distance) * robot.max_speed
        
        # Apply movement
        new_x = robot.position.x + vx * dt
        new_y = robot.position.y + vy * dt
        
        # Check if valid
        new_pos = Position(new_x, new_y)
        if self.layout.is_valid_position(new_pos):
            robot.position = new_pos
            robot.state = RobotState.MOVING
            robot.velocity = robot.max_speed
        else:
            # Try to find alternative path (simple: stop)
            robot.target = None
            robot.state = RobotState.IDLE
    
    def _check_collisions(self):
        """Check for collisions between robots."""
        robots_list = list(self.robots.values())
        
        for i, robot1 in enumerate(robots_list):
            for robot2 in robots_list[i+1:]:
                dist = robot1.position.distance_to(robot2.position)
                min_dist = robot1.collision_radius + robot2.collision_radius
                
                if dist < min_dist:
                    self.collisions += 1
                    # Simple resolution: both stop
                    robot1.target = None
                    robot2.target = None
                    robot1.state = RobotState.IDLE
                    robot2.state = RobotState.IDLE
    
    def _generate_task(self):
        """Generate a new task."""
        task_types = ["pick", "move", "charge", "maintain"]
        task_type = random.choice(task_types)
        
        if task_type == "pick":
            source = Zone.RECEIVING
            target = Zone.PICKING
        elif task_type == "move":
            source = random.choice([Zone.STORAGE, Zone.PICKING])
            target = random.choice([Zone.STORAGE, Zone.SHIPPING])
        elif task_type == "charge":
            source = None
            target = Zone.CHARGING
        else:
            source = None
            target = None
        
        task_id = f"T{len(self.tasks):05d}"
        task = Task(
            task_id=task_id,
            task_type=task_type,
            priority=random.randint(1, 10),
            source_zone=source,
            target_zone=target,
        )
        
        self.tasks[task_id] = task
        self.task_queue.append(task)
        self.total_tasks += 1
    
    def _cleanup_completed_tasks(self):
        """Remove completed tasks from queue."""
        self.task_queue = [t for t in self.task_queue if t.status != TaskStatus.COMPLETED]
    
    # --- Zone Leases ---
    
    def request_lease(self, robot_id: str, zone: Zone, duration_ms: int) -> bool:
        """Request a lease for a zone."""
        zone_key = zone.value
        
        if zone_key in self.zone_leases:
            # Check existing lease
            existing = self.zone_leases[zone_key]
            if existing["expires_at"] > time.time():
                # Check if same robot
                if existing["robot_id"] != robot_id:
                    # Try to outbid
                    if existing["bid"] >= 100:  # Simple bid comparison
                        return False
        
        # Grant lease
        self.zone_leases[zone_key] = {
            "robot_id": robot_id,
            "expires_at": time.time() + duration_ms / 1000,
            "bid": 100 + random.randint(0, 50),
        }
        
        # Update robot
        if robot_id in self.robots:
            self.robots[robot_id].zone_lease = zone_key
        
        return True
    
    # --- Metrics ---
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get fleet metrics."""
        running_time = time.time() - self.start_time
        
        active_robots = sum(1 for r in self.robots.values() 
                         if r.state in [RobotState.MOVING, RobotState.WORKING])
        idle_robots = sum(1 for r in self.robots.values() 
                        if r.state == RobotState.IDLE)
        charging_robots = sum(1 for r in self.robots.values() 
                           if r.state == RobotState.CHARGING)
        
        avg_battery = sum(r.battery for r in self.robots.values()) / len(self.robots)
        
        pending_tasks = sum(1 for t in self.tasks.values() 
                         if t.status == TaskStatus.PENDING)
        active_tasks = sum(1 for t in self.tasks.values() 
                          if t.is_active)
        
        return {
            "timestamp": time.time(),
            "runningTime": running_time,
            "totalRobots": len(self.robots),
            "activeRobots": active_robots,
            "idleRobots": idle_robots,
            "chargingRobots": charging_robots,
            "averageBattery": avg_battery,
            "totalTasks": self.total_tasks,
            "completedTasks": self.completed_tasks,
            "failedTasks": self.failed_tasks,
            "pendingTasks": pending_tasks,
            "activeTasks": active_tasks,
            "collisions": self.collisions,
            "tasksPerMinute": (self.completed_tasks / max(running_time / 60, 0.001)),
        }
    
    def get_robot_states(self) -> List[Dict[str, Any]]:
        """Get state of all robots."""
        return [r.to_dict() for r in self.robots.values()]


# ============================================================================
# Visualizer (Text-based)
# ============================================================================

class FleetVisualizer:
    """Text-based visualization of the warehouse."""
    
    def __init__(self, layout: WarehouseLayout):
        self.layout = layout
        self.symbols = {
            RobotType.AMR: "A",
            RobotType.PICKER: "P",
            RobotType.CONVEYOR: "C",
            RobotType.FORKLIFT: "F",
        }
    
    def render(self, fleet: FleetController) -> str:
        """Render warehouse state as ASCII."""
        width = 80
        height = 30
        
        # Create grid
        grid = [[" " for _ in range(width)] for _ in range(height)]
        
        # Scale positions
        scale_x = width / self.layout.width
        scale_y = height / self.layout.height
        
        # Draw zones (simplified)
        zone_markers = {
            Zone.RECEIVING: "RCV",
            Zone.STORAGE: "STG",
            Zone.PICKING: "PCK",
            Zone.SHIPPING: "SHP",
            Zone.CHARGING: "CHG",
            Zone.CORRIDOR: "---",
        }
        
        # Draw obstacles
        for ox, oy, ow, oh in self.layout.obstacles:
            x1 = int(ox * scale_x)
            y1 = int(oy * scale_y)
            x2 = int((ox + ow) * scale_x)
            y2 = int((oy + oh) * scale_y)
            
            for y in range(max(0, y1), min(height, y2)):
                for x in range(max(0, x1), min(width, x2)):
                    grid[y][x] = "#"
        
        # Draw robots
        for robot in fleet.robots.values():
            x = int(robot.position.x * scale_x)
            y = int(robot.position.y * scale_y)
            
            if 0 <= x < width and 0 <= y < height:
                symbol = self.symbols.get(robot.robot_type, "?")
                
                # Add state indicator
                if robot.state == RobotState.MOVING:
                    symbol = symbol.lower()
                elif robot.state == RobotState.CHARGING:
                    symbol = "o"
                
                grid[y][x] = symbol
        
        # Build output
        lines = []
        lines.append("=" * width)
        lines.append("P-MCP Fleet Simulator - Warehouse View")
        lines.append("=" * width)
        
        # Add grid
        for row in grid:
            lines.append("|" + "".join(row) + "|")
        
        # Add legend and metrics
        lines.append("-" * width)
        lines.append("Legend: A=AMR, P=Picker, C=Conveyor, F=Forklift, #=Obstacle")
        
        metrics = fleet.get_metrics()
        lines.append(f"Time: {metrics['runningTime']:.1f}s | " +
                    f"Robots: {metrics['activeRobots']}/{metrics['totalRobots']} active | " +
                    f"Battery: {metrics['averageBattery']:.1f}%")
        lines.append(f"Tasks: {metrics['completedTasks']} done, " +
                    f"{metrics['pendingTasks']} pending, " +
                    f"{metrics['collisions']} collisions")
        
        return "\n".join(lines)


# ============================================================================
# Main
# ============================================================================

async def run_simulation(num_robots: int = 50, visualize: bool = True, 
                        steps: int = 1000, dt: float = 0.1):
    """Run the fleet simulation."""
    logger.info(f"Starting simulation with {num_robots} robots")
    
    # Create layout and fleet
    layout = WarehouseLayout(100, 60)
    fleet = FleetController(layout, num_robots)
    
    # Create visualizer
    visualizer = FleetVisualizer(layout) if visualize else None
    
    # Run simulation
    for step in range(steps):
        # Update
        fleet.update(dt)
        
        # Render periodically
        if visualize and step % 50 == 0:
            if visualizer:
                print("\033[2J\033[H")  # Clear screen
                print(visualizer.render(fleet))
            
            # Print metrics
            metrics = fleet.get_metrics()
            print(f"\nStep {step}: {metrics['completedTasks']} tasks completed, " +
                  f"{metrics['activeRobots']} active robots")
        
        # Small delay for readability
        if visualize:
            await asyncio.sleep(0.01)
    
    # Final metrics
    logger.info("Simulation complete!")
    final_metrics = fleet.get_metrics()
    logger.info(f"Final metrics: {final_metrics}")
    
    return final_metrics


async def run_test():
    """Run test mode with quick validation."""
    logger.info("Running fleet simulation test...")
    
    # Quick simulation
    layout = WarehouseLayout(50, 30)
    fleet = FleetController(layout, 10)
    
    # Run a few updates
    for _ in range(100):
        fleet.update(0.1)
    
    # Verify
    assert len(fleet.robots) == 10, "Should have 10 robots"
    assert len(fleet.tasks) > 0, "Should have tasks"
    
    metrics = fleet.get_metrics()
    assert metrics['totalRobots'] == 10, "Metrics should show 10 robots"
    
    logger.info("All tests passed!")
    logger.info(f"Metrics: {metrics}")


def main():
    parser = argparse.ArgumentParser(description="P-MCP Fleet Simulator")
    parser.add_argument("--robots", type=int, default=50, 
                       help="Number of robots")
    parser.add_argument("--visualize", action="store_true",
                       help="Enable visualization")
    parser.add_argument("--steps", type=int, default=1000,
                       help="Number of simulation steps")
    parser.add_argument("--dt", type=float, default=0.1,
                       help="Time step")
    parser.add_argument("--test", action="store_true",
                       help="Run test mode")
    
    args = parser.parse_args()
    
    if args.test:
        asyncio.run(run_test())
    else:
        asyncio.run(run_simulation(
            args.robots, args.visualize, args.steps, args.dt
        ))


if __name__ == "__main__":
    main()