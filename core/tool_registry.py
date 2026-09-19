import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


class Risk(Enum):
    SAFE = "safe"
    CAUTION = "caution"
    DANGEROUS = "dangerous"
    FORBIDDEN = "forbidden"


class ToolSource(Enum):
    BUILTIN = "builtin"
    ACTION = "action"
    PLUGIN = "plugin"
    EXTERNAL = "external"


class SchedulingMode(Enum):
    BLOCKING = "blocking"
    NON_BLOCKING = "non_blocking"
    SILENT = "silent"
    INTERRUPT = "interrupt"


@dataclass
class ToolParameter:
    """Represents a single parameter for a tool."""
    name: str
    type: str
    description: str = ""
    required: bool = False
    default: Any = None
    enum: list = field(default_factory=list)
    min_value: Optional[float] = None
    max_value: Optional[float] = None

    def validate(self, value: Any) -> tuple[bool, str]:
        """Validate a value against this parameter's constraints.

        Returns:
            Tuple of (is_valid, error_message). error_message is empty if valid.
        """
        if value is None:
            if self.required:
                return False, f"Parameter '{self.name}' is required"
            return True, ""

        if self.enum and value not in self.enum:
            return False, f"Parameter '{self.name}' must be one of {self.enum}, got '{value}'"

        type_map = {
            "string": str,
            "int": int,
            "integer": int,
            "float": (int, float),  # int is also valid float
            "number": (int, float),
            "bool": bool,
            "boolean": bool,
            "list": list,
            "dict": dict,
        }

        expected = type_map.get(self.type)
        if expected:
            if not isinstance(value, expected):
                return False, f"Parameter '{self.name}' must be of type {self.type}, got {type(value).__name__}"

        if self.min_value is not None and isinstance(value, (int, float)):
            if value < self.min_value:
                return False, f"Parameter '{self.name}' must be >= {self.min_value}, got {value}"

        if self.max_value is not None and isinstance(value, (int, float)):
            if value > self.max_value:
                return False, f"Parameter '{self.name}' must be <= {self.max_value}, got {value}"

        return True, ""


@dataclass
class ToolSchema:
    """Schema defining a tool's interface and metadata."""
    name: str
    description: str
    parameters: list[ToolParameter]
    risk_level: Risk = Risk.SAFE
    permissions: list[str] = field(default_factory=list)
    timeout: int = 30
    confirmation_required: bool = False
    reversible: bool = False
    verification_strategy: str = "none"
    failure_modes: list[str] = field(default_factory=list)
    scheduling: SchedulingMode = SchedulingMode.BLOCKING
    source: ToolSource = ToolSource.BUILTIN
    category: str = "general"

    def validate_arguments(self, args: dict) -> tuple[bool, list[str]]:
        """Validate arguments against this tool's parameter schema.

        Returns:
            Tuple of (is_valid, list_of_errors).
        """
        errors = []

        for param in self.parameters:
            if param.required and param.name not in args:
                errors.append(f"Missing required parameter: '{param.name}'")
                continue

            if param.name in args:
                is_valid, msg = param.validate(args[param.name])
                if not is_valid:
                    errors.append(msg)

        known_names = {p.name for p in self.parameters}
        for key in args:
            if key not in known_names:
                errors.append(f"Unknown parameter: '{key}'")

        return len(errors) == 0, errors

    def to_dict(self) -> dict:
        """Serialize the schema to a dictionary."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": [
                {
                    "name": p.name,
                    "type": p.type,
                    "description": p.description,
                    "required": p.required,
                    "default": p.default,
                    "enum": p.enum,
                    "min_value": p.min_value,
                    "max_value": p.max_value,
                }
                for p in self.parameters
            ],
            "risk_level": self.risk_level.value,
            "permissions": self.permissions,
            "timeout": self.timeout,
            "confirmation_required": self.confirmation_required,
            "reversible": self.reversible,
            "verification_strategy": self.verification_strategy,
            "failure_modes": self.failure_modes,
            "scheduling": self.scheduling.value,
            "source": self.source.value,
            "category": self.category,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ToolSchema":
        """Deserialize a schema from a dictionary."""
        parameters = [
            ToolParameter(
                name=p["name"],
                type=p["type"],
                description=p.get("description", ""),
                required=p.get("required", False),
                default=p.get("default", None),
                enum=p.get("enum", []),
                min_value=p.get("min_value", None),
                max_value=p.get("max_value", None),
            )
            for p in data.get("parameters", [])
        ]
        return cls(
            name=data["name"],
            description=data.get("description", ""),
            parameters=parameters,
            risk_level=Risk(data.get("risk_level", "safe")),
            permissions=data.get("permissions", []),
            timeout=data.get("timeout", 30),
            confirmation_required=data.get("confirmation_required", False),
            reversible=data.get("reversible", False),
            verification_strategy=data.get("verification_strategy", "none"),
            failure_modes=data.get("failure_modes", []),
            scheduling=SchedulingMode(data.get("scheduling", "blocking")),
            source=ToolSource(data.get("source", "builtin")),
            category=data.get("category", "general"),
        )


@dataclass
class RegisteredTool:
    """A tool registered in the registry with its schema and handler."""
    schema: ToolSchema
    handler: Callable[..., Any]
    enabled: bool = True


class ToolRegistry:
    """Central registry for tool schemas and handlers."""

    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}
        self._categories: dict[str, list[str]] = {}

    def register(self, schema: ToolSchema, handler: Callable[..., Any]) -> bool:
        """Register a tool with its schema and handler.

        Returns:
            True if registration succeeded, False if tool name already exists.
        """
        if schema.name in self._tools:
            logger.warning(f"Tool '{schema.name}' is already registered")
            return False

        self._tools[schema.name] = RegisteredTool(schema=schema, handler=handler)
        self._categories.setdefault(schema.category, []).append(schema.name)
        logger.info(f"Registered tool '{schema.name}'")
        return True

    def unregister(self, name: str) -> bool:
        """Unregister a tool by name.

        Returns:
            True if the tool was removed, False if not found.
        """
        if name not in self._tools:
            logger.warning(f"Tool '{name}' not found for unregistration")
            return False

        tool = self._tools.pop(name)
        cat_list = self._categories.get(tool.schema.category, [])
        if name in cat_list:
            cat_list.remove(name)
            if not cat_list:
                del self._categories[tool.schema.category]

        logger.info(f"Unregistered tool '{name}'")
        return True

    def get(self, name: str) -> Optional[RegisteredTool]:
        """Get a registered tool by name."""
        return self._tools.get(name)

    def get_schema(self, name: str) -> Optional[ToolSchema]:
        """Get a tool's schema by name."""
        tool = self._tools.get(name)
        return tool.schema if tool else None

    def has(self, name: str) -> bool:
        """Check if a tool is registered."""
        return name in self._tools

    def enable(self, name: str) -> bool:
        """Enable a registered tool.

        Returns:
            True if the tool was enabled, False if not found.
        """
        if name not in self._tools:
            logger.warning(f"Tool '{name}' not found for enabling")
            return False

        self._tools[name].enabled = True
        logger.info(f"Enabled tool '{name}'")
        return True

    def disable(self, name: str) -> bool:
        """Disable a registered tool.

        Returns:
            True if the tool was disabled, False if not found.
        """
        if name not in self._tools:
            logger.warning(f"Tool '{name}' not found for disabling")
            return False

        self._tools[name].enabled = False
        logger.info(f"Disabled tool '{name}'")
        return True

    def list_tools(
        self,
        category: Optional[str] = None,
        source: Optional[ToolSource] = None,
        enabled_only: bool = True,
    ) -> list[ToolSchema]:
        """List tools filtered by category, source, and enabled status."""
        results = []
        for tool in self._tools.values():
            if enabled_only and not tool.enabled:
                continue
            if category and tool.schema.category != category:
                continue
            if source and tool.schema.source != source:
                continue
            results.append(tool.schema)
        return results

    def list_categories(self) -> list[str]:
        """List all registered tool categories."""
        return list(self._categories.keys())

    def validate_arguments(self, name: str, args: dict) -> tuple[bool, list[str]]:
        """Validate arguments for a registered tool.

        Returns:
            Tuple of (is_valid, list_of_errors).
        """
        schema = self.get_schema(name)
        if not schema:
            return False, [f"Tool '{name}' not found"]
        return schema.validate_arguments(args)

    def execute(self, name: str, args: dict) -> dict:
        """Execute a tool with the given arguments.

        Returns:
            Dict with 'success', 'output', and optionally 'errors'.
        """
        tool = self.get(name)
        if not tool:
            return {"success": False, "output": None, "errors": [f"Tool '{name}' not found"]}

        if not tool.enabled:
            return {"success": False, "output": None, "errors": [f"Tool '{name}' is disabled"]}

        is_valid, errors = tool.schema.validate_arguments(args)
        if not is_valid:
            return {"success": False, "output": None, "errors": errors}

        try:
            result = tool.handler(**args)
            return {"success": True, "output": result, "errors": []}
        except Exception as e:
            logger.error(f"Error executing tool '{name}': {e}")
            return {"success": False, "output": None, "errors": [str(e)]}

    def get_schemas_for_model(self) -> list[dict]:
        """Return schemas as dicts for model consumption (only enabled tools)."""
        return [t.schema.to_dict() for t in self._tools.values() if t.enabled]
