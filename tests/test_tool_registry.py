"""
Tests for core/tool_registry.py — Tool Registry with schema validation.
"""

import pytest
from core.tool_registry import (
    ToolRegistry, ToolSchema, ToolParameter, RegisteredTool,
    Risk, ToolSource, SchedulingMode,
)


@pytest.fixture
def registry():
    return ToolRegistry()


@pytest.fixture
def sample_schema():
    return ToolSchema(
        name="open_app",
        description="Open an application",
        parameters=[
            ToolParameter(name="app_name", type="string", required=True),
            ToolParameter(name="maximized", type="bool", default=False),
        ],
        risk_level=Risk.CAUTION,
        category="system",
        source=ToolSource.BUILTIN,
    )


@pytest.fixture
def sample_handler():
    def handler(app_name: str, maximized: bool = False) -> dict:
        return {"status": "success", "app": app_name}
    return handler


class TestToolParameter:
    def test_required_param(self):
        param = ToolParameter(name="test", type="string", required=True)
        valid, err = param.validate(None)
        assert not valid
        assert "required" in err

    def test_optional_param_none(self):
        param = ToolParameter(name="test", type="string", required=False)
        valid, err = param.validate(None)
        assert valid

    def test_type_validation_string(self):
        param = ToolParameter(name="test", type="string")
        valid, _ = param.validate("hello")
        assert valid
        valid, err = param.validate(123)
        assert not valid

    def test_type_validation_int(self):
        param = ToolParameter(name="test", type="int")
        valid, _ = param.validate(42)
        assert valid
        valid, _ = param.validate(3.14)  # float is not int
        assert not valid

    def test_type_validation_float(self):
        param = ToolParameter(name="test", type="float")
        valid, _ = param.validate(3.14)
        assert valid
        valid, _ = param.validate(42)  # int is also float
        assert valid

    def test_type_validation_bool(self):
        param = ToolParameter(name="test", type="bool")
        valid, _ = param.validate(True)
        assert valid
        valid, _ = param.validate("true")
        assert not valid

    def test_enum_validation(self):
        param = ToolParameter(name="test", type="string", enum=["a", "b", "c"])
        valid, _ = param.validate("a")
        assert valid
        valid, err = param.validate("d")
        assert not valid

    def test_min_value(self):
        param = ToolParameter(name="test", type="int", min_value=0)
        valid, _ = param.validate(5)
        assert valid
        valid, err = param.validate(-1)
        assert not valid

    def test_max_value(self):
        param = ToolParameter(name="test", type="int", max_value=100)
        valid, _ = param.validate(50)
        assert valid
        valid, err = param.validate(101)
        assert not valid


class TestToolSchema:
    def test_validate_arguments_valid(self, sample_schema):
        valid, errors = sample_schema.validate_arguments({"app_name": "notepad"})
        assert valid
        assert len(errors) == 0

    def test_validate_arguments_missing_required(self, sample_schema):
        valid, errors = sample_schema.validate_arguments({})
        assert not valid
        assert any("app_name" in e for e in errors)

    def test_validate_arguments_unknown_param(self, sample_schema):
        valid, errors = sample_schema.validate_arguments({
            "app_name": "notepad",
            "unknown": "value",
        })
        assert not valid
        assert any("unknown" in e for e in errors)

    def test_validate_arguments_type_error(self, sample_schema):
        valid, errors = sample_schema.validate_arguments({"app_name": 123})
        assert not valid

    def test_to_dict(self, sample_schema):
        d = sample_schema.to_dict()
        assert d["name"] == "open_app"
        assert d["risk_level"] == "caution"
        assert len(d["parameters"]) == 2

    def test_from_dict(self, sample_schema):
        d = sample_schema.to_dict()
        restored = ToolSchema.from_dict(d)
        assert restored.name == "open_app"
        assert restored.risk_level == Risk.CAUTION
        assert len(restored.parameters) == 2


class TestToolRegistry:
    def test_register_tool(self, registry, sample_schema, sample_handler):
        result = registry.register(sample_schema, sample_handler)
        assert result is True
        assert registry.has("open_app")

    def test_unregister_tool(self, registry, sample_schema, sample_handler):
        registry.register(sample_schema, sample_handler)
        assert registry.has("open_app")
        result = registry.unregister("open_app")
        assert result is True
        assert not registry.has("open_app")

    def test_unregister_nonexistent(self, registry):
        result = registry.unregister("nonexistent")
        assert result is False

    def test_get_tool(self, registry, sample_schema, sample_handler):
        registry.register(sample_schema, sample_handler)
        tool = registry.get("open_app")
        assert tool is not None
        assert tool.schema.name == "open_app"

    def test_get_schema(self, registry, sample_schema, sample_handler):
        registry.register(sample_schema, sample_handler)
        schema = registry.get_schema("open_app")
        assert schema is not None
        assert schema.name == "open_app"

    def test_enable_disable(self, registry, sample_schema, sample_handler):
        registry.register(sample_schema, sample_handler)
        assert registry.disable("open_app")
        tool = registry.get("open_app")
        assert not tool.enabled
        assert registry.enable("open_app")
        assert tool.enabled

    def test_list_tools(self, registry, sample_schema, sample_handler):
        registry.register(sample_schema, sample_handler)
        tools = registry.list_tools()
        assert len(tools) == 1
        assert tools[0].name == "open_app"

    def test_list_tools_by_category(self, registry, sample_schema, sample_handler):
        registry.register(sample_schema, sample_handler)
        tools = registry.list_tools(category="system")
        assert len(tools) == 1
        tools = registry.list_tools(category="other")
        assert len(tools) == 0

    def test_list_tools_by_source(self, registry, sample_schema, sample_handler):
        registry.register(sample_schema, sample_handler)
        tools = registry.list_tools(source=ToolSource.BUILTIN)
        assert len(tools) == 1
        tools = registry.list_tools(source=ToolSource.PLUGIN)
        assert len(tools) == 0

    def test_list_categories(self, registry, sample_schema, sample_handler):
        registry.register(sample_schema, sample_handler)
        cats = registry.list_categories()
        assert "system" in cats

    def test_validate_arguments(self, registry, sample_schema, sample_handler):
        registry.register(sample_schema, sample_handler)
        valid, errors = registry.validate_arguments("open_app", {"app_name": "notepad"})
        assert valid

    def test_validate_arguments_tool_not_found(self, registry):
        valid, errors = registry.validate_arguments("nonexistent", {})
        assert not valid

    def test_execute_success(self, registry, sample_schema, sample_handler):
        registry.register(sample_schema, sample_handler)
        result = registry.execute("open_app", {"app_name": "notepad"})
        assert result["success"] is True
        assert result["output"]["app"] == "notepad"

    def test_execute_tool_not_found(self, registry):
        result = registry.execute("nonexistent", {})
        assert result["success"] is False

    def test_execute_validation_error(self, registry, sample_schema, sample_handler):
        registry.register(sample_schema, sample_handler)
        result = registry.execute("open_app", {})
        assert result["success"] is False
        assert len(result["errors"]) > 0

    def test_execute_disabled_tool(self, registry, sample_schema, sample_handler):
        registry.register(sample_schema, sample_handler)
        registry.disable("open_app")
        result = registry.execute("open_app", {"app_name": "notepad"})
        assert result["success"] is False

    def test_get_schemas_for_model(self, registry, sample_schema, sample_handler):
        registry.register(sample_schema, sample_handler)
        schemas = registry.get_schemas_for_model()
        assert len(schemas) == 1
        assert schemas[0]["name"] == "open_app"
