#!/usr/bin/env python3
"""
P-MCP ROS2 Parameter Server
===========================

Manages ROS2 parameters for robots.

Usage:
    python -m ros2_bridge.param_server --namespace /robot1
"""

import asyncio
import logging
import time
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from enum import Enum

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s [%(name)s] %(message)s'
)
logger = logging.getLogger("pmcp-param-server")


class ParameterType(Enum):
    """ROS2 parameter types."""
    DOUBLE = "double"
    INT = "integer"
    STRING = "string"
    BOOL = "boolean"
    BYTES = "bytes"
    DOUBLE_ARRAY = "double_array"
    INT_ARRAY = "integer_array"
    STRING_ARRAY = "string_array"


@dataclass
class Parameter:
    """A ROS2 parameter."""
    name: str
    value: Any
    param_type: ParameterType
    namespace: str = ""
    description: str = ""
    read_only: bool = False
    dynamic: bool = False
    modified_at: float = field(default_factory=time.time)
    created_at: float = field(default_factory=time.time)


@dataclass
class ParameterGroup:
    """A group of related parameters."""
    group_id: str
    name: str
    namespace: str
    parameters: Dict[str, Parameter] = field(default_factory=dict)
    description: str = ""
    created_at: float = field(default_factory=time.time)


class ParameterManager:
    """Manages ROS2 parameters for a robot."""

    def __init__(self, namespace: str = ""):
        self.namespace = namespace
        self._parameters: Dict[str, Parameter] = {}
        self._groups: Dict[str, ParameterGroup] = {}
        self._lock = threading.Lock()
        self._history: List[Dict[str, Any]] = []
        self._callbacks: Dict[str, List[callable]] = {}
        self._initialize_default_parameters()

    def _initialize_default_parameters(self):
        """Initialize default parameters for a robot."""
        defaults = [
            Parameter("robot_name", "robot", ParameterType.STRING, self.namespace, "Robot name"),
            Parameter("max_velocity", 1.0, ParameterType.DOUBLE, self.namespace, "Max velocity m/s"),
            Parameter("max_acceleration", 0.5, ParameterType.DOUBLE, self.namespace, "Max acceleration"),
            Parameter("obstacle_avoidance_enabled", True, ParameterType.BOOL, self.namespace, "Enable obstacle avoidance"),
            Parameter("localization_method", "amcl", ParameterType.STRING, self.namespace, "Localization method"),
            Parameter("map_frame", "map", ParameterType.STRING, self.namespace, "Map frame"),
            Parameter("odom_frame", "odom", ParameterType.STRING, self.namespace, "Odometry frame"),
            Parameter("base_frame", "base_link", ParameterType.STRING, self.namespace, "Base frame"),
            Parameter("laser_topic", "/scan", ParameterType.STRING, self.namespace, "Laser scan topic"),
            Parameter("cmd_vel_topic", "/cmd_vel", ParameterType.STRING, self.namespace, "Command velocity topic"),
        ]

        with self._lock:
            for param in defaults:
                self._parameters[param.name] = param

    def set_parameter(self, name: str, value: Any, param_type: ParameterType, description: str = "") -> Parameter:
        """Set a parameter value."""
        with self._lock:
            param = self._parameters.get(name)
            if param and param.read_only:
                raise ValueError(f"Parameter {name} is read-only")

            param = Parameter(
                name=name,
                value=value,
                param_type=param_type,
                namespace=self.namespace,
                description=description,
                modified_at=time.time(),
            )
            self._parameters[name] = param

            self._history.append({
                "action": "set",
                "name": name,
                "value": value,
                "timestamp": time.time(),
            })

            self._notify_callbacks(name, value)

            return param

    def get_parameter(self, name: str) -> Optional[Parameter]:
        """Get a parameter."""
        with self._lock:
            return self._parameters.get(name)

    def get_parameter_value(self, name: str, default: Any = None) -> Any:
        """Get parameter value with default."""
        param = self.get_parameter(name)
        return param.value if param else default

    def delete_parameter(self, name: str) -> bool:
        """Delete a parameter."""
        with self._lock:
            param = self._parameters.get(name)
            if param and param.read_only:
                return False

            if name in self._parameters:
                del self._parameters[name]
                self._history.append({
                    "action": "delete",
                    "name": name,
                    "timestamp": time.time(),
                })
                return True
            return False

    def list_parameters(self, pattern: str = "*") -> List[Parameter]:
        """List parameters matching a pattern."""
        with self._lock:
            if pattern == "*":
                return list(self._parameters.values())
            return [p for p in self._parameters.values() if pattern in p.name]

    def get_parameter_tree(self) -> Dict[str, Any]:
        """Get full parameter tree as dictionary."""
        with self._lock:
            return {
                name: {
                    "value": param.value,
                    "type": param.param_type.value,
                    "description": param.description,
                    "read_only": param.read_only,
                    "dynamic": param.dynamic,
                    "modified_at": param.modified_at,
                }
                for name, param in self._parameters.items()
            }

    def create_group(self, group_id: str, name: str, description: str = "") -> ParameterGroup:
        """Create a parameter group."""
        with self._lock:
            group = ParameterGroup(
                group_id=group_id,
                name=name,
                namespace=self.namespace,
                description=description,
            )
            self._groups[group_id] = group
            return group

    def add_to_group(self, group_id: str, param_name: str) -> bool:
        """Add a parameter to a group."""
        with self._lock:
            group = self._groups.get(group_id)
            param = self._parameters.get(param_name)
            if not group or not param:
                return False

            group.parameters[param_name] = param
            return True

    def get_group(self, group_id: str) -> Optional[ParameterGroup]:
        """Get a parameter group."""
        with self._lock:
            return self._groups.get(group_id)

    def list_groups(self) -> List[ParameterGroup]:
        """List all parameter groups."""
        with self._lock:
            return list(self._groups.values())

    def subscribe(self, param_name: str, callback: callable):
        """Subscribe to parameter changes."""
        if param_name not in self._callbacks:
            self._callbacks[param_name] = []
        self._callbacks[param_name].append(callback)

    def unsubscribe(self, param_name: str, callback: callable):
        """Unsubscribe from parameter changes."""
        if param_name in self._callbacks:
            self._callbacks[param_name] = [cb for cb in self._callbacks[param_name] if cb != callback]

    def _notify_callbacks(self, param_name: str, value: Any):
        """Notify subscribed callbacks of parameter change."""
        if param_name in self._callbacks:
            for callback in self._callbacks[param_name]:
                try:
                    callback(param_name, value)
                except Exception as e:
                    logger.error(f"Callback error for {param_name}: {e}")

    def get_history(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get parameter change history."""
        return self._history[-limit:]

    def export_parameters(self) -> Dict[str, Any]:
        """Export all parameters as JSON-compatible dict."""
        return {
            "namespace": self.namespace,
            "parameters": self.get_parameter_tree(),
            "groups": {
                g.group_id: {
                    "name": g.name,
                    "description": g.description,
                    "parameters": list(g.parameters.keys()),
                }
                for g in self._groups.values()
            },
            "exported_at": time.time(),
        }

    def import_parameters(self, data: Dict[str, Any]):
        """Import parameters from exported data."""
        params = data.get("parameters", {})
        for name, info in params.items():
            try:
                param_type = ParameterType(info.get("type", "string"))
                self.set_parameter(name, info.get("value"), param_type, info.get("description", ""))
            except Exception as e:
                logger.error(f"Failed to import parameter {name}: {e}")


class MultiRobotParamManager:
    """Manages parameters for multiple robots."""

    def __init__(self):
        self._managers: Dict[str, ParameterManager] = {}
        self._lock = threading.Lock()

    def get_manager(self, namespace: str) -> ParameterManager:
        """Get or create parameter manager for a namespace."""
        with self._lock:
            if namespace not in self._managers:
                self._managers[namespace] = ParameterManager(namespace)
            return self._managers[namespace]

    def list_namespaces(self) -> List[str]:
        """List all managed namespaces."""
        with self._lock:
            return list(self._managers.keys())

    def broadcast_parameter(self, param_name: str, value: Any, param_type: ParameterType):
        """Set a parameter on all robots."""
        with self._lock:
            for manager in self._managers.values():
                try:
                    manager.set_parameter(param_name, value, param_type)
                except Exception as e:
                    logger.error(f"Failed to set {param_name} on {manager.namespace}: {e}")


async def main():
    """Run parameter server."""
    manager = ParameterManager("/robot1")

    manager.set_parameter("max_velocity", 1.5, ParameterType.DOUBLE, "Max velocity")
    manager.set_parameter("robot_name", "turtlebot", ParameterType.STRING, "Robot name")

    params = manager.list_parameters()
    logger.info(f"Total parameters: {len(params)}")

    tree = manager.get_parameter_tree()
    logger.info(f"Parameter tree: {tree}")

    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())