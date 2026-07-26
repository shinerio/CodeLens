"""Runtime enforcement shared by model-visible function tools."""

import json
from typing import Any

from agents import FunctionTool
from agents.tool_context import ToolContext


def reject_unknown_arguments(tool: FunctionTool) -> FunctionTool:
    """Reject fields forbidden by the advertised strict schema at the local boundary."""

    invoke = tool.on_invoke_tool
    expected = frozenset(tool.params_json_schema.get("properties", {}))

    async def invoke_strict(context: ToolContext[Any], arguments: str) -> Any:
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            return await invoke(context, arguments)
        if isinstance(parsed, dict):
            unknown = sorted(str(name) for name in parsed if name not in expected)
            if unknown:
                fields = ", ".join(unknown)
                return f"Tool arguments contain unsupported fields: {fields}"
        return await invoke(context, arguments)

    tool.on_invoke_tool = invoke_strict
    return tool
