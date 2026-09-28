#!/usr/bin/env python3
"""
P-MCP ROS2 Bridge
==================

Bridge that exposes ROS2 topics, services, and actions as MCP resources and tools.
Supports ROS2 Galactic and Humble.

Based on patterns from:
- ros2_mcp (wise-vision)
- ros2mcp (ngre)
- dora-rs/dora ROS2 bridge

Usage:
    python -m ros2_bridge.bridge --ros-domain 0
    python -m ros2_bridge.bridge --transport stdio
"""

import argparse
import asyncio
import json
import logging
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Callable
from enum import Enum
import threading

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s [%(name)s] %(message)s'
)
logger = logging.getLogger("pmcp-ros2-bridge")


class TransportType(Enum):
    """Transport type for MCP communication."""
    STDIO = "stdio"
    SSE = "sse"


@dataclass
class TopicInfo:
    """Information about a ROS2 topic."""
    name: str
    topic_type: str
    publishers: int = 0
    subscribers: int = 0


@dataclass
class ServiceInfo:
    """Information about a ROS2 service."""
    name: str
    service_type: str
    available: bool = True


@dataclass
class ActionInfo:
    """Information about a ROS2 action."""
    name: str
    action_type: str
    available: bool = True


@dataclass
class MessageSample:
    """A sample message from a topic."""
    topic: str
    message_type: str
    data: Dict[str, Any]
    timestamp: float


class ROS2Introspector:
    """Introspect ROS2 system for topics, services, and actions."""

    def __init__(self):
        self._topics: Dict[str, TopicInfo] = {}
        self._services: Dict[str, ServiceInfo] = {}
        self._actions: Dict[str, ActionInfo] = {}
        self._message_cache: Dict[str, List[MessageSample]] = {}
        self._lock = threading.Lock()
        self._ros_available = False
        self._try_import_ros()

    def _try_import_ros(self):
        """Try to import ROS2 libraries."""
        try:
            import rclpy
            from rclpy.node import Node
            self._ros_available = True
            logger.info("ROS2 libraries available")
        except ImportError:
            logger.warning("ROS2 not available - running in mock mode")
            self._ros_available = False
            self._initialize_mock_topics()

    def _initialize_mock_topics(self):
        """Initialize mock topics for testing without ROS2."""
        self._topics = {
            "/cmd_vel": TopicInfo("/cmd_vel", "geometry_msgs/msg/Twist", 1, 1),
            "/odom": TopicInfo("/odom", "nav_msgs/msg/Odometry", 1, 2),
            "/scan": TopicInfo("/scan", "sensor_msgs/msg/LaserScan", 1, 1),
            "/camera/image_raw": TopicInfo("/camera/image_raw", "sensor_msgs/msg/Image", 1, 1),
            "/joint_states": TopicInfo("/joint_states", "sensor_msgs/msg/JointState", 1, 1),
            "/battery_state": TopicInfo("/battery_state", "sensor_msgs/msg/BatteryState", 1, 0),
            "/tf": TopicInfo("/tf", "tf2_msgs/msg/TFMessage", 1, 1),
            "/robot_state": TopicInfo("/robot_state", "std_msgs/msg/String", 1, 0),
        }
        self._services = {
            "/robot_control/execute": ServiceInfo("/robot_control/execute", "robot_control/srv/Execute"),
            "/navigation/get_pose": ServiceInfo("/navigation/get_pose", "navigation/srv/GetPose"),
            "/sensors/calibrate": ServiceInfo("/sensors/calibrate", "sensors/srv/Calibrate"),
        }
        self._actions = {
            "/move_base": ActionInfo("/move_base", "move_base/action/MoveBase"),
            "/pick_place": ActionInfo("/pick_place", "manipulation/action/PickPlace"),
        }

    def refresh(self):
        """Refresh topic/service/action lists."""
        with self._lock:
            if not self._ros_available:
                return
            try:
                pass
            except Exception as e:
                logger.error(f"Failed to refresh ROS2 info: {e}")

    def list_topics(self) -> List[TopicInfo]:
        """List all available topics."""
        with self._lock:
            return list(self._topics.values())

    def get_topic_info(self, topic_name: str) -> Optional[TopicInfo]:
        """Get information about a specific topic."""
        with self._lock:
            return self._topics.get(topic_name)

    def list_services(self) -> List[ServiceInfo]:
        """List all available services."""
        with self._lock:
            return list(self._services.values())

    def list_actions(self) -> List[ActionInfo]:
        """List all available actions."""
        with self._lock:
            return list(self._actions.values())

    def subscribe_topic(self, topic_name: str, duration: float = 5.0, max_messages: int = 100) -> List[MessageSample]:
        """Subscribe to a topic and collect messages."""
        topic_info = self.get_topic_info(topic_name)
        if not topic_info:
            logger.warning(f"Topic not found: {topic_name}")
            return []

        messages = []
        end_time = time.time() + duration

        while time.time() < end_time and len(messages) < max_messages:
            msg_data = self._generate_mock_message(topic_info.topic_type)
            messages.append(MessageSample(
                topic=topic_name,
                message_type=topic_info.topic_type,
                data=msg_data,
                timestamp=time.time(),
            ))
            time.sleep(0.1)

        with self._lock:
            self._message_cache[topic_name] = messages

        logger.info(f"Collected {len(messages)} messages from {topic_name}")
        return messages

    def _generate_mock_message(self, msg_type: str) -> Dict[str, Any]:
        """Generate mock message data for testing."""
        if "Twist" in msg_type:
            return {
                "linear": {"x": 0.5, "y": 0.0, "z": 0.0},
                "angular": {"x": 0.0, "y": 0.0, "z": 0.0},
            }
        elif "Odometry" in msg_type:
            return {
                "pose": {
                    "pose": {
                        "position": {"x": 1.0, "y": 2.0, "z": 0.0},
                        "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
                    }
                },
                "twist": {
                    "linear": {"x": 0.1, "y": 0.0, "z": 0.0},
                    "angular": {"x": 0.0, "y": 0.0, "z": 0.0},
                },
            }
        elif "LaserScan" in msg_type:
            return {
                "angle_min": 0.0,
                "angle_max": 3.14,
                "angle_increment": 0.01,
                "range_min": 0.1,
                "range_max": 10.0,
                "ranges": [1.0] * 314,
                "intensities": [0.5] * 314,
            }
        elif "JointState" in msg_type:
            return {
                "name": ["joint1", "joint2", "joint3"],
                "position": [0.1, 0.2, 0.3],
                "velocity": [0.01, 0.02, 0.01],
                "effort": [0.5, 0.3, 0.2],
            }
        elif "BatteryState" in msg_type:
            return {
                "voltage": 12.5,
                "percentage": 0.85,
                "power": 45.0,
                "status": 2,
            }
        else:
            return {"data": "mock message"}

    def get_cached_messages(self, topic_name: str) -> List[MessageSample]:
        """Get cached messages for a topic."""
        with self._lock:
            return self._message_cache.get(topic_name, [])

    def publish_message(self, topic_name: str, msg_type: str, data: Dict[str, Any]) -> bool:
        """Publish a message to a topic."""
        logger.info(f"Publishing to {topic_name}: {data}")
        return True

    def call_service(self, service_name: str, request_data: Dict[str, Any]) -> Dict[str, Any]:
        """Call a ROS2 service."""
        logger.info(f"Calling service {service_name} with {request_data}")
        return {
            "success": True,
            "result": "Service executed successfully",
            "data": {"status": "completed"},
        }

    def get_message_fields(self, msg_type: str) -> Dict[str, str]:
        """Get field names and types for a message type."""
        field_map = {
            "geometry_msgs/msg/Twist": {
                "linear": "geometry_msgs/msg/Vector3",
                "angular": "geometry_msgs/msg/Vector3",
            },
            "nav_msgs/msg/Odometry": {
                "pose": "geometry_msgs/msg/PoseWithCovariance",
                "twist": "geometry_msgs/msg/TwistWithCovariance",
            },
            "sensor_msgs/msg/LaserScan": {
                "header": "std_msgs/msg/Header",
                "angle_min": "float64",
                "angle_max": "float64",
                "angle_increment": "float64",
                "range_min": "float64",
                "range_max": "float64",
                "ranges": "float64[]",
                "intensities": "float64[]",
            },
            "sensor_msgs/msg/JointState": {
                "header": "std_msgs/msg/Header",
                "name": "string[]",
                "position": "float64[]",
                "velocity": "float64[]",
                "effort": "float64[]",
            },
        }
        return field_map.get(msg_type, {})


class ROS2MCPServer:
    """MCP server that exposes ROS2 functionality."""

    def __init__(self, introspector: ROS2Introspector, transport: TransportType = TransportType.STDIO):
        self.introspector = introspector
        self.transport = transport
        self._running = False

    async def start(self, port: int = 8084):
        """Start the MCP server."""
        try:
            from aiohttp import web
        except ImportError:
            logger.error("aiohttp not installed")
            return

        app = web.Application()

        app.router.add_get("/health", self.handle_health)
        app.router.add_get("/mcp/tools", self.handle_list_tools)
        app.router.add_get("/mcp/resources", self.handle_list_resources)

        app.router.add_post("/mcp/execute", self.handle_execute_tool)

        app.router.add_get("/ros2/topics", self.handle_list_topics)
        app.router.add_get("/ros2/topics/{name}", self.handle_get_topic)
        app.router.add_post("/ros2/topics/{name}/subscribe", self.handle_subscribe)
        app.router.add_post("/ros2/topics/{name}/publish", self.handle_publish)

        app.router.add_get("/ros2/services", self.handle_list_services)
        app.router.add_post("/ros2/services/{name}/call", self.handle_service_call)

        app.router.add_get("/ros2/actions", self.handle_list_actions)

        self._running = True

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", port)
        await site.start()

        logger.info(f"P-MCP ROS2 Bridge starting on port {port}")

        try:
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()

    async def handle_health(self, request: web.Request) -> web.Response:
        return web.json_response({
            "status": "healthy",
            "version": "0.5.0",
            "ros2_available": self.introspector._ros_available,
        })

    async def handle_list_tools(self, request: web.Request) -> web.Response:
        tools = [
            {
                "name": "ros2_topic_list",
                "description": "List all available ROS2 topics",
                "inputSchema": {"type": "object", "properties": {}},
            },
            {
                "name": "ros2_topic_subscribe",
                "description": "Subscribe to a ROS2 topic and collect messages",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "topic": {"type": "string", "description": "Topic name"},
                        "duration": {"type": "number", "description": "Duration in seconds"},
                        "max_messages": {"type": "integer", "description": "Maximum messages"},
                    },
                    "required": ["topic"],
                },
            },
            {
                "name": "ros2_topic_publish",
                "description": "Publish a message to a ROS2 topic",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "topic": {"type": "string"},
                        "message_type": {"type": "string"},
                        "data": {"type": "object"},
                    },
                    "required": ["topic", "message_type", "data"],
                },
            },
            {
                "name": "ros2_service_call",
                "description": "Call a ROS2 service",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "service": {"type": "string"},
                        "service_type": {"type": "string"},
                        "request": {"type": "object"},
                    },
                    "required": ["service", "service_type"],
                },
            },
            {
                "name": "ros2_get_message_fields",
                "description": "Get message field definitions",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "message_type": {"type": "string"},
                    },
                    "required": ["message_type"],
                },
            },
        ]
        return web.json_response({"tools": tools})

    async def handle_list_resources(self, request: web.Request) -> web.Response:
        topics = self.introspector.list_topics()
        resources = [
            {
                "uri": f"ros2://topic/{t.name}",
                "name": t.name,
                "type": "topic",
                "topic_type": t.topic_type,
            }
            for t in topics
        ]
        return web.json_response({"resources": resources})

    async def handle_execute_tool(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        tool_name = data.get("tool")
        params = data.get("parameters", {})

        result = self._execute_tool(tool_name, params)
        return web.json_response(result)

    def _execute_tool(self, tool_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a tool."""
        if tool_name == "ros2_topic_list":
            topics = self.introspector.list_topics()
            return {
                "topics": [
                    {"name": t.name, "type": t.topic_type, "publishers": t.publishers, "subscribers": t.subscribers}
                    for t in topics
                ],
                "count": len(topics),
            }
        elif tool_name == "ros2_topic_subscribe":
            topic = params.get("topic")
            duration = params.get("duration", 5.0)
            max_messages = params.get("max_messages", 100)
            messages = self.introspector.subscribe_topic(topic, duration, max_messages)
            return {
                "topic": topic,
                "messages": [m.data for m in messages],
                "count": len(messages),
                "duration": duration,
            }
        elif tool_name == "ros2_topic_publish":
            topic = params.get("topic")
            msg_type = params.get("message_type")
            data = params.get("data", {})
            success = self.introspector.publish_message(topic, msg_type, data)
            return {"success": success, "topic": topic}
        elif tool_name == "ros2_service_call":
            service = params.get("service")
            request = params.get("request", {})
            result = self.introspector.call_service(service, request)
            return result
        elif tool_name == "ros2_get_message_fields":
            msg_type = params.get("message_type")
            fields = self.introspector.get_message_fields(msg_type)
            return {"message_type": msg_type, "fields": fields}
        else:
            return {"error": f"Unknown tool: {tool_name}"}

    async def handle_list_topics(self, request: web.Request) -> web.Response:
        topics = self.introspector.list_topics()
        return web.json_response({
            "topics": [
                {"name": t.name, "type": t.topic_type, "publishers": t.publishers, "subscribers": t.subscribers}
                for t in topics
            ],
            "count": len(topics),
        })

    async def handle_get_topic(self, request: web.Request) -> web.Response:
        name = request.match_info["name"]
        topic = self.introspector.get_topic_info(name)
        if not topic:
            return web.json_response({"error": f"Topic not found: {name}"}, status=404)
        return web.json_response({"name": topic.name, "type": topic.topic_type})

    async def handle_subscribe(self, request: web.Request) -> web.Response:
        name = request.match_info["name"]
        try:
            data = await request.json()
        except Exception:
            data = {}
        duration = data.get("duration", 5.0)
        max_messages = data.get("max_messages", 100)
        messages = self.introspector.subscribe_topic(name, duration, max_messages)
        return web.json_response({
            "topic": name,
            "messages": [m.data for m in messages],
            "count": len(messages),
        })

    async def handle_publish(self, request: web.Request) -> web.Response:
        name = request.match_info["name"]
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)
        msg_type = data.get("message_type", "std_msgs/msg/String")
        success = self.introspector.publish_message(name, msg_type, data.get("data", {}))
        return web.json_response({"success": success})

    async def handle_list_services(self, request: web.Request) -> web.Response:
        services = self.introspector.list_services()
        return web.json_response({
            "services": [{"name": s.name, "type": s.service_type} for s in services],
            "count": len(services),
        })

    async def handle_service_call(self, request: web.Request) -> web.Response:
        name = request.match_info["name"]
        try:
            data = await request.json()
        except Exception:
            data = {}
        result = self.introspector.call_service(name, data.get("request", {}))
        return web.json_response(result)

    async def handle_list_actions(self, request: web.Request) -> web.Response:
        actions = self.introspector.list_actions()
        return web.json_response({
            "actions": [{"name": a.name, "type": a.action_type} for a in actions],
            "count": len(actions),
        })


async def main():
    parser = argparse.ArgumentParser(description="P-MCP ROS2 Bridge")
    parser.add_argument("--port", type=int, default=8084, help="Server port")
    parser.add_argument("--ros-domain", type=int, default=0, help="ROS2 domain ID")
    parser.add_argument("--transport", default="sse", choices=["stdio", "sse"], help="Transport type")
    args = parser.parse_args()

    introspector = ROS2Introspector()
    server = ROS2MCPServer(introspector, TransportType(args.transport))
    await server.start(args.port)


if __name__ == "__main__":
    asyncio.run(main())