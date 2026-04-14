import json
import os
import time
from typing import Any, Optional

import httpx
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.responses import Response

app = FastAPI(title="AI Backend Demo (Ollama + OpenAI + Gemini)")

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
MODEL_CHEAP = os.getenv("MODEL_CHEAP", "llama3")
MODEL_SMART = os.getenv("MODEL_SMART", "llama3")

DEFAULT_PROVIDER = os.getenv("PROVIDER", "ollama").lower()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
OPENAI_MODEL_CHEAP = os.getenv("OPENAI_MODEL_CHEAP", "gpt-4o-mini")
OPENAI_MODEL_SMART = os.getenv("OPENAI_MODEL_SMART", "gpt-4o")

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "").strip()
GEMINI_API_BASE = os.getenv(
    "GEMINI_API_BASE",
    "https://generativelanguage.googleapis.com/v1beta",
).rstrip("/")
GEMINI_MODEL_CHEAP = os.getenv("GEMINI_MODEL_CHEAP", "gemini-2.0-flash")
GEMINI_MODEL_SMART = os.getenv("GEMINI_MODEL_SMART", "gemini-1.5-pro")

# Prometheus metrics
ai_requests_total = Counter(
    "ai_requests_total",
    "Total AI requests passing through the AI backend (post-gateway).",
    ["endpoint", "provider", "model", "status_code"],
)

ai_request_latency_seconds = Histogram(
    "ai_request_latency_seconds",
    "Latency for AI endpoints.",
    ["endpoint", "provider", "model"],
)

ai_pii_redactions_total = Counter(
    "ai_pii_redactions_total",
    "Total PII redactions performed by the gateway policy (reported via header).",
)

ai_moderation_decisions_total = Counter(
    "ai_moderation_decisions_total",
    "Moderation decisions from /ai/moderate.",
    ["decision"],
)


def burn_cpu(ms: int) -> None:
    """Busy-loop for ~ms milliseconds (demo-only)."""
    if ms <= 0:
        return
    end = time.perf_counter() + (ms / 1000.0)
    x = 0.0
    while time.perf_counter() < end:
        x = (x + 1.000001) * 0.999999


def normalize_provider(name: Optional[str]) -> str:
    p = (name or DEFAULT_PROVIDER).lower().strip()
    if p not in ("ollama", "openai", "gemini"):
        raise HTTPException(
            status_code=400,
            detail="provider must be one of: ollama, openai, gemini",
        )
    return p


def resolve_tier(mode: Optional[str], tier: Optional[str]) -> str:
    if tier is not None:
        t = tier.lower().strip()
        if t not in ("cheap", "smart"):
            raise HTTPException(
                status_code=400,
                detail="tier must be 'cheap' (fast/economical) or 'smart' (higher quality/cost)",
            )
        return t
    if mode == "smart":
        return "smart"
    return "cheap"


def model_for_provider(provider: str, tier: str) -> str:
    if provider == "ollama":
        return MODEL_SMART if tier == "smart" else MODEL_CHEAP
    if provider == "openai":
        return OPENAI_MODEL_SMART if tier == "smart" else OPENAI_MODEL_CHEAP
    if provider == "gemini":
        return GEMINI_MODEL_SMART if tier == "smart" else GEMINI_MODEL_CHEAP
    raise HTTPException(status_code=400, detail=f"unknown provider: {provider}")


def assert_provider_configured(provider: str) -> None:
    if provider == "openai" and not OPENAI_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="OpenAI is not configured: set OPENAI_API_KEY (e.g. via Kubernetes secret ai-llm-credentials).",
        )
    if provider == "gemini" and not GOOGLE_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="Gemini is not configured: set GOOGLE_API_KEY / Google AI Studio key.",
        )


async def call_ollama(model: str, system_prompt: str, user_prompt: str) -> str:
    url = f"{OLLAMA_BASE_URL}/api/chat"
    payload = {
        "model": model,
        "stream": False,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(url, json=payload)
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Ollama error: {resp.text[:500]}")
        data = resp.json()
        return data["message"]["content"]


async def call_openai(model: str, system_prompt: str, user_prompt: str) -> str:
    assert_provider_configured("openai")
    url = f"{OPENAI_BASE_URL}/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}"}
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(url, json=payload, headers=headers)
        if resp.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"OpenAI error ({resp.status_code}): {resp.text[:500]}",
            )
        data = resp.json()
        return data["choices"][0]["message"]["content"]


async def call_gemini(model: str, system_prompt: str, user_prompt: str) -> str:
    assert_provider_configured("gemini")
    url = f"{GEMINI_API_BASE}/models/{model}:generateContent"
    params = {"key": GOOGLE_API_KEY}
    body: dict[str, Any] = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [
            {
                "role": "user",
                "parts": [{"text": user_prompt}],
            }
        ],
    }
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(url, params=params, json=body)
        if resp.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"Gemini error ({resp.status_code}): {resp.text[:500]}",
            )
        data = resp.json()
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as e:
            raise HTTPException(
                status_code=502,
                detail=f"Unexpected Gemini response shape: {data!r[:800]}",
            ) from e


async def generate_text(provider: str, model: str, system_prompt: str, user_prompt: str) -> str:
    if provider == "ollama":
        return await call_ollama(model, system_prompt, user_prompt)
    if provider == "openai":
        return await call_openai(model, system_prompt, user_prompt)
    if provider == "gemini":
        return await call_gemini(model, system_prompt, user_prompt)
    raise HTTPException(status_code=400, detail=f"unknown provider: {provider}")


class LLMParams(BaseModel):
    """Pick cloud + price/quality tier per request (demo-friendly)."""

    provider: Optional[str] = Field(
        default=None,
        description="ollama | openai | gemini. Defaults to PROVIDER env.",
    )
    tier: Optional[str] = Field(
        default=None,
        description="'cheap' (economical) or 'smart' (higher cost/quality). Overrides legacy mode when set.",
    )


class SummarizeRequest(LLMParams):
    text: str
    mode: Optional[str] = "auto"
    max_words: Optional[int] = 50
    cpu_burn_ms: Optional[int] = 0


class TranslateRequest(LLMParams):
    text: str
    target_language: str


class ModerateRequest(LLMParams):
    text: str


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "default_provider": DEFAULT_PROVIDER,
        "ollama": {
            "cheap": MODEL_CHEAP,
            "smart": MODEL_SMART,
            "base_url": OLLAMA_BASE_URL,
        },
        "openai": {
            "cheap": OPENAI_MODEL_CHEAP,
            "smart": OPENAI_MODEL_SMART,
            "configured": bool(OPENAI_API_KEY),
            "base_url": OPENAI_BASE_URL,
        },
        "gemini": {
            "cheap": GEMINI_MODEL_CHEAP,
            "smart": GEMINI_MODEL_SMART,
            "configured": bool(GOOGLE_API_KEY),
            "api_base": GEMINI_API_BASE,
        },
    }


def observe_pii_header(request: Request) -> None:
    raw = request.headers.get("x-pii-redactions", "0")
    try:
        n = int(raw)
    except Exception:
        n = 0
    if n > 0:
        ai_pii_redactions_total.inc(n)


def record_metrics(endpoint: str, provider: str, model: str, status_code: int, elapsed: float) -> None:
    ai_requests_total.labels(
        endpoint=endpoint,
        provider=provider,
        model=model,
        status_code=str(status_code),
    ).inc()
    ai_request_latency_seconds.labels(
        endpoint=endpoint,
        provider=provider,
        model=model,
    ).observe(elapsed)


@app.post("/ai/moderate")
async def moderate(req: ModerateRequest):
    start = time.perf_counter()
    endpoint = "/ai/moderate"
    provider = normalize_provider(req.provider)
    assert_provider_configured(provider)
    tier = resolve_tier(None, req.tier)
    model = model_for_provider(provider, tier)

    system_prompt = (
        "Classify the following user input for safety. "
        "Return JSON with allowed:true/false and reason."
    )
    try:
        raw = await generate_text(provider, model, system_prompt, req.text)
    except HTTPException:
        raise
    except Exception:
        raw = json.dumps({"allowed": True, "reason": "mock: LLM unavailable"})

    allowed = True
    reason = "no reason given"
    try:
        data = json.loads(raw)
        allowed = bool(data.get("allowed", True))
        reason = str(data.get("reason", "no reason given"))
    except Exception:
        allowed = True
        reason = "LLM returned non-JSON; assuming allowed"

    decision = "allowed" if allowed else "blocked"
    ai_moderation_decisions_total.labels(decision=decision).inc()

    elapsed = time.perf_counter() - start
    record_metrics(endpoint, provider, model, 200, elapsed)
    return {
        "provider": provider,
        "tier": tier,
        "model_used": model,
        "allowed": allowed,
        "reason": reason,
    }


@app.post("/ai/summarize")
async def summarize(req: SummarizeRequest, request: Request):
    start = time.perf_counter()
    endpoint = "/ai/summarize"
    provider = normalize_provider(req.provider)
    assert_provider_configured(provider)
    tier = resolve_tier(req.mode, req.tier)
    model = model_for_provider(provider, tier)

    observe_pii_header(request)

    burn_cpu(req.cpu_burn_ms or 0)

    system_prompt = f"You are a summarization assistant. Summarize in {req.max_words} words."
    try:
        result = await generate_text(provider, model, system_prompt, req.text)
    except HTTPException:
        raise
    except Exception:
        snippet = (req.text or "")[: max(10, min(200, (req.max_words or 50) * 6))].strip()
        result = f"[mock-summary] {snippet}"

    elapsed = time.perf_counter() - start
    record_metrics(endpoint, provider, model, 200, elapsed)
    return {
        "provider": provider,
        "tier": tier,
        "model_used": model,
        "summary": result,
    }


@app.post("/ai/translate")
async def translate(req: TranslateRequest, request: Request):
    start = time.perf_counter()
    endpoint = "/ai/translate"
    provider = normalize_provider(req.provider)
    assert_provider_configured(provider)
    # Legacy: translate always used the "smart" model unless tier is set explicitly.
    if req.tier is None:
        tier = "smart"
    else:
        tier = resolve_tier(None, req.tier)
    model = model_for_provider(provider, tier)

    observe_pii_header(request)

    system_prompt = f"You are a translator to {req.target_language}."
    try:
        result = await generate_text(provider, model, system_prompt, req.text)
    except HTTPException:
        raise
    except Exception:
        result = f"[mock-translation to {req.target_language}] {req.text}"

    elapsed = time.perf_counter() - start
    record_metrics(endpoint, provider, model, 200, elapsed)
    return {
        "provider": provider,
        "tier": tier,
        "model_used": model,
        "translated_text": result,
        "target_language": req.target_language,
    }
