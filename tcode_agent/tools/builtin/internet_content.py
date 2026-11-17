"""Desktop entry point for the operator-pinned, public Agent Reach reader."""
from __future__ import annotations

import asyncio
import json
import os

from ...types import ToolResult
from ..base import ParameterSchema, Tool, ToolContext


class InternetContentParameters(ParameterSchema):
    action: str = "channels"
    channel: str = ""
    value: str = ""
    limit: int = 5


class InternetContentTool(Tool):
    @classmethod
    def register(cls) -> None:
        from ..registry import ToolRegistry

        ToolRegistry.register(cls())

    @property
    def id(self) -> str:
        return "internet_content"

    @property
    def description(self) -> str:
        return ("Read public structured internet content through approved channels. "
                "Use action=channels to discover enabled actions and parameters. "
                "For an action marked desktop-browser, delegate to agent-browser rather than calling this tool for the read. "
                "Subtitle reads existing platform captions. Transcribe sends audio to Groq; "
                "only use it when the user explicitly requests audio transcription. "
                "Does not log in, import cookies or operate a browser. "
                "For papers and PDF downloads use the dedicated literature tools.")

    @property
    def parameters(self) -> type:
        return InternetContentParameters

    def available_to_agent(self, agent_name: str) -> bool:
        return os.environ.get("TCODE_DESKTOP_MANAGED") == "1" and bool(os.environ.get("TCODE_AGENT_REACH_PYTHON"))

    async def execute(self, args: dict, context: ToolContext) -> ToolResult:
        from ...server.agent_reach_mcp import channel_catalog, read_public_channel

        if not self.available_to_agent(context.agent):
            return ToolResult(title=self.id, output="", error="公开互联网渠道未配置。")
        params = InternetContentParameters(**args)
        if params.action == "channels":
            result = channel_catalog()
        else:
            result = await asyncio.to_thread(read_public_channel, params.channel, params.action, params.value, params.limit)
        return ToolResult(title=self.id, output=json.dumps(result, ensure_ascii=False), error=result.get("error"))
