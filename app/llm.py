"""A small tool-calling loop on the OpenAI Responses API (through Portkey).

gpt-6-luna rejects `temperature`, so it is never sent. Each agent gets:
  - a fixed system prompt from app/prompts/<name>.md (kept first, so it can be cached)
  - a list of Python tools, described with JSON schemas
  - a hard cap on tool rounds and on output tokens per call
Every step is recorded in `trace` so the UI and the report can show what each
agent did.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from openai import BadRequestError, OpenAI

from . import config
from .meter import Meter, usage_from_response

PROMPTS = Path(__file__).resolve().parent / "prompts"
_client: OpenAI | None = None


class AgentLoopLimit(Exception):
    pass


class ContentFiltered(Exception):
    """The model provider's content filter refused the request (e.g. a jailbreak attempt)."""


def client() -> OpenAI:
    global _client
    if _client is None:
        if not config.api_key():
            raise RuntimeError("PORTKEY_API_KEY not found. Put it in .env in this folder or the folder above.")
        _client = OpenAI(api_key=config.api_key(), base_url=config.PORTKEY_BASE_URL, timeout=90, max_retries=2)
    return _client


def prompt(name: str) -> str:
    return (PROMPTS / f"{name}.md").read_text()


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    fn: Callable[..., Any]

    def spec(self) -> dict:
        return {"type": "function", "name": self.name, "description": self.description,
                "parameters": self.parameters, "strict": False}


@dataclass
class AgentResult:
    text: str
    data: Any = None
    trace: list[dict] = field(default_factory=list)
    tool_outputs: list[Any] = field(default_factory=list)


def _clip(obj: Any, limit: int = 600) -> str:
    s = obj if isinstance(obj, str) else json.dumps(obj, default=str)
    return s if len(s) <= limit else s[:limit] + "..."


def run_agent(
    name: str,
    user_input: str,
    meter: Meter,
    tools: list[Tool] | None = None,
    schema: dict | None = None,
    max_rounds: int = config.MAX_TOOL_ROUNDS,
    history: list[dict] | None = None,
) -> AgentResult:
    tools = tools or []
    by_name = {t.name: t for t in tools}
    instructions = prompt(name)
    # Earlier chat turns (plain text only, already cleaned and clipped by the caller).
    items: list[Any] = [{"role": h["role"], "content": h["content"]} for h in (history or [])]
    items.append({"role": "user", "content": user_input})
    trace: list[dict] = []
    outputs: list[Any] = []

    for round_no in range(max_rounds + 1):
        meter.check()
        kwargs: dict[str, Any] = {
            "model": config.MODEL,
            "instructions": instructions,
            "input": items,
            "max_output_tokens": config.MAX_OUTPUT_TOKENS_PER_CALL,
            "prompt_cache_key": f"appraisal-commons-{name}",
        }
        if name in config.REASONING_EFFORT:
            kwargs["reasoning"] = {"effort": config.REASONING_EFFORT[name]}
        if tools:
            kwargs["tools"] = [t.spec() for t in tools]
            if round_no == max_rounds:  # last round: must answer, no more tools
                kwargs["tool_choice"] = "none"
        if schema:
            kwargs["text"] = {"format": {"type": "json_schema", "name": f"{name}_output", "schema": schema, "strict": True}}
        try:
            resp = client().responses.create(**kwargs)
        except BadRequestError as exc:
            if "content_filter" in str(exc) or "content management policy" in str(exc):
                trace.append({"agent": name, "step": "provider_filter", "round": round_no})
                raise ContentFiltered(name) from exc
            raise
        usage = usage_from_response(resp)
        rec = meter.add(name, usage)
        trace.append({"agent": name, "step": "model_call", "round": round_no, "usage": usage, "cost_usd": rec["cost_usd"]})

        calls = [o for o in resp.output if getattr(o, "type", None) == "function_call"]
        if not calls:
            text = resp.output_text or ""
            data = None
            if schema:
                try:
                    data = json.loads(text)
                except json.JSONDecodeError:
                    data = None
            trace.append({"agent": name, "step": "answer", "preview": _clip(text, 400)})
            return AgentResult(text=text, data=data, trace=trace, tool_outputs=outputs)

        items.extend(resp.output)
        for call in calls:
            tool = by_name.get(call.name)
            try:
                args = json.loads(call.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            if tool is None:
                result: Any = {"error": f"unknown tool {call.name}"}
            else:
                try:
                    result = tool.fn(**args)
                except Exception as exc:  # tool errors go back to the model, not the user
                    result = {"error": f"{type(exc).__name__}: {exc}"}
            outputs.append(result)
            trace.append({"agent": name, "step": "tool_call", "tool": call.name, "args": args, "result_preview": _clip(result)})
            items.append({"type": "function_call_output", "call_id": call.call_id, "output": json.dumps(result, default=str)})

    raise AgentLoopLimit(f"{name} used all {max_rounds} tool rounds without answering")
