\
from __future__ import annotations
import os
import time
from dataclasses import dataclass, field
from .links import normalize_sources, sources_from_text

@dataclass
class ApiResult:
    response: str
    model_observed: str | None
    latency_ms: int
    status: str = "ok"
    error: str | None = None
    sources: list[dict] = field(default_factory=list)

    def __post_init__(self):
        self.sources = normalize_sources([*self.sources, *sources_from_text(self.response)])

def ask_openai(prompt: str, provider_cfg: dict) -> ApiResult:
    from openai import OpenAI

    started = time.perf_counter()
    try:
        client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        response = client.responses.create(
            model=provider_cfg["model"],
            input=[{"role": "user", "content": prompt}],
            store=False,
            max_output_tokens=provider_cfg.get("max_output_tokens", 1200),
        )
        return ApiResult(
            response=response.output_text or "",
            model_observed=getattr(response, "model", None),
            latency_ms=round((time.perf_counter() - started) * 1000),
        )
    except Exception as e:
        return ApiResult(
            response="",
            model_observed=None,
            latency_ms=round((time.perf_counter() - started) * 1000),
            status="error",
            error=str(e),
        )

def ask_gemini(prompt: str, provider_cfg: dict) -> ApiResult:
    """
    Implemented but DISABLED in config.yaml until GEMINI_API_KEY is added.
    generate_content is used as a single-turn call: no prior chat history is sent.
    """
    from google import genai
    from google.genai import types

    started = time.perf_counter()
    try:
        client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        response = client.models.generate_content(
            model=provider_cfg["model"],
            contents=prompt,
            config=types.GenerateContentConfig(
                max_output_tokens=provider_cfg.get("max_output_tokens", 1200),
            ),
        )
        return ApiResult(
            response=response.text or "",
            model_observed=provider_cfg["model"],
            latency_ms=round((time.perf_counter() - started) * 1000),
        )
    except Exception as e:
        return ApiResult(
            response="",
            model_observed=None,
            latency_ms=round((time.perf_counter() - started) * 1000),
            status="error",
            error=str(e),
        )

def ask_anthropic(prompt: str, provider_cfg: dict) -> ApiResult:
    """
    Implemented but DISABLED in config.yaml until ANTHROPIC_API_KEY is added.
    Messages requests are stateless unless previous turns are explicitly supplied.
    """
    from anthropic import Anthropic

    started = time.perf_counter()
    try:
        client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        msg = client.messages.create(
            model=provider_cfg["model"],
            max_tokens=provider_cfg.get("max_output_tokens", 1200),
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(
            block.text for block in msg.content
            if getattr(block, "type", None) == "text"
        )
        return ApiResult(
            response=text,
            model_observed=getattr(msg, "model", provider_cfg["model"]),
            latency_ms=round((time.perf_counter() - started) * 1000),
        )
    except Exception as e:
        return ApiResult(
            response="",
            model_observed=None,
            latency_ms=round((time.perf_counter() - started) * 1000),
            status="error",
            error=str(e),
        )

API_ADAPTERS = {
    "openai": ask_openai,
    "gemini": ask_gemini,
    "anthropic": ask_anthropic,
}
