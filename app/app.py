import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from starlette.responses import HTMLResponse, Response

app = FastAPI(title="AI Backend Demo (AI Gateway patterns)")

_STATIC_DIR = Path(__file__).resolve().parent / "static"

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
MODEL_CHEAP = os.getenv("MODEL_CHEAP", "llama3")
MODEL_SMART = os.getenv("MODEL_SMART", "llama3")

DEFAULT_PROVIDER = os.getenv("PROVIDER", "ollama").lower()
CLOUD_PROVIDER_FOR_PREMIUM = os.getenv("CLOUD_PROVIDER_FOR_PREMIUM", "openai").lower()

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

FAILOVER_ENABLED = os.getenv("AI_FAILOVER_ENABLED", "true").lower() in ("1", "true", "yes")
DEMO_SIMULATE_OPENAI_STATUS = os.getenv("DEMO_SIMULATE_OPENAI_STATUS", "").strip()
# When true, never call OpenAI/Gemini HTTP APIs (Ollama only). Use for bill-free live demos.
CLOUD_LLM_CALLS_DISABLED = os.getenv("CLOUD_LLM_CALLS_DISABLED", "").lower() in ("1", "true", "yes")

# Guardrails / governance (demo-friendly, env-configurable)
GUARDRAILS_ENABLED = os.getenv("AI_GUARDRAILS_ENABLED", "true").lower() in ("1", "true", "yes")
# block | route_to_local | off
GUARDRAILS_MODE = os.getenv("AI_GUARDRAILS_MODE", "route_to_local").lower().strip() or "route_to_local"
GUARDRAILS_FORCE_LOCAL_PROVIDER = os.getenv("AI_GUARDRAILS_FORCE_LOCAL_PROVIDER", "ollama").lower().strip() or "ollama"
GUARDRAILS_FORCE_LOCAL_TIER = os.getenv("AI_GUARDRAILS_FORCE_LOCAL_TIER", "cheap").lower().strip() or "cheap"


def _parse_float(env_name: str, default: float) -> float:
    raw = os.getenv(env_name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


# Budgeting per tenant (per pod, in-memory; intended for live demos)
BUDGET_ENABLED = os.getenv("AI_BUDGET_ENABLED", "true").lower() in ("1", "true", "yes")
APP_BUDGET_USD_PER_HOUR = max(0.0, _parse_float("AI_APP_BUDGET_USD_PER_HOUR", 0.02))
APP_BUDGET_WINDOW_SECONDS = int(max(60, _parse_float("AI_APP_BUDGET_WINDOW_SECONDS", 3600)))

# Caching (per pod, in-memory)
CACHE_ENABLED = os.getenv("AI_CACHE_ENABLED", "true").lower() in ("1", "true", "yes")
CACHE_TTL_SECONDS = int(max(5, _parse_float("AI_CACHE_TTL_SECONDS", 120)))
CACHE_MAX_ENTRIES = int(max(10, _parse_float("AI_CACHE_MAX_ENTRIES", 500)))

# Circuit breaker (per provider, per pod)
CIRCUIT_ENABLED = os.getenv("AI_CIRCUIT_BREAKER_ENABLED", "true").lower() in ("1", "true", "yes")
CIRCUIT_FAIL_THRESHOLD = int(max(1, _parse_float("AI_CIRCUIT_FAIL_THRESHOLD", 3)))
CIRCUIT_OPEN_SECONDS = int(max(5, _parse_float("AI_CIRCUIT_OPEN_SECONDS", 45)))


class LLMUpstreamError(Exception):
    """Transient LLM failure — try next provider in failover chain."""

    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail


_demo_quota_lock = threading.Lock()
_openai_demo_success_count = 0
_gemini_demo_success_count = 0


def _parse_demo_quota(env_name: str) -> int:
    raw = os.getenv(env_name, "").strip()
    if not raw:
        return 0
    try:
        return max(0, int(raw))
    except ValueError:
        return 0


OPENAI_DEMO_QUOTA = _parse_demo_quota("OPENAI_DEMO_QUOTA")
GEMINI_DEMO_QUOTA = _parse_demo_quota("GEMINI_DEMO_QUOTA")


def _demo_enforce_quota(provider: str) -> None:
    """Block before a real HTTP call when successful-call budget for this provider is exhausted."""
    if provider == "openai":
        limit = OPENAI_DEMO_QUOTA
    elif provider == "gemini":
        limit = GEMINI_DEMO_QUOTA
    else:
        return
    if limit <= 0:
        return
    with _demo_quota_lock:
        used = _openai_demo_success_count if provider == "openai" else _gemini_demo_success_count
        if used >= limit:
            raise LLMUpstreamError(
                429,
                f"Demo quota reached for {provider}: limit is {limit} successful "
                f"API call(s) (OPENAI_DEMO_QUOTA / GEMINI_DEMO_QUOTA).",
            )


def _demo_record_success(provider: str) -> None:
    global _openai_demo_success_count, _gemini_demo_success_count
    if provider == "openai":
        limit = OPENAI_DEMO_QUOTA
    elif provider == "gemini":
        limit = GEMINI_DEMO_QUOTA
    else:
        return
    if limit <= 0:
        return
    with _demo_quota_lock:
        if provider == "openai":
            _openai_demo_success_count += 1
        else:
            _gemini_demo_success_count += 1


def _demo_quota_snapshot() -> dict[str, Any]:
    with _demo_quota_lock:
        return {
            "openai": {
                "limit": OPENAI_DEMO_QUOTA or None,
                "successful_calls": _openai_demo_success_count,
            },
            "gemini": {
                "limit": GEMINI_DEMO_QUOTA or None,
                "successful_calls": _gemini_demo_success_count,
            },
        }


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

ai_llm_failover_total = Counter(
    "ai_llm_failover_total",
    "Failover events after an upstream LLM error (429/5xx).",
    ["from_provider", "to_provider"],
)

ai_requests_per_application = Counter(
    "ai_requests_per_application",
    "Requests by X-Application-Id (demo: per-app budgets / noisy neighbor).",
    ["application_id", "endpoint"],
)

ai_estimated_input_chars = Histogram(
    "ai_estimated_input_chars",
    "Input text size for rough capacity / token budgeting demos.",
    ["endpoint", "provider"],
)

# Rough FinOps signal: ~chars/4 “tokens” × static USD/M list prices (not vendor invoices).
ai_estimated_request_cost_usd = Histogram(
    "ai_estimated_request_cost_usd",
    "Heuristic per-request LLM cost estimate in USD (by API route).",
    ["endpoint", "provider", "model"],
    buckets=(
        0.0,
        1e-7,
        1e-6,
        5e-6,
        1e-5,
        5e-5,
        1e-4,
        5e-4,
        0.001,
        0.005,
        0.01,
        0.05,
        0.1,
        0.5,
        float("inf"),
    ),
)

# Cumulative FinOps counters keyed by provider + model (integer increments only; USD as micro-dollars).
ai_estimated_cost_microdollars_total = Counter(
    "ai_estimated_cost_microdollars_total",
    "Cumulative heuristic spend: USD × 1e6 (divide by 1e6 for dollars). Labels: provider, model.",
    ["provider", "model"],
)

ai_estimated_input_tokens_total = Counter(
    "ai_estimated_input_tokens_total",
    "Cumulative estimated input tokens (~chars/4). Labels: provider, model.",
    ["provider", "model"],
)

ai_estimated_output_tokens_total = Counter(
    "ai_estimated_output_tokens_total",
    "Cumulative estimated output tokens (~chars/4). Labels: provider, model.",
    ["provider", "model"],
)

ai_guardrail_events_total = Counter(
    "ai_guardrail_events_total",
    "Guardrail events (prompt injection/exfil patterns) observed by the backend.",
    ["action", "rule"],
)

ai_budget_block_total = Counter(
    "ai_budget_block_total",
    "Requests blocked due to per-application budget limits.",
    ["application_id", "endpoint"],
)

ai_cache_requests_total = Counter(
    "ai_cache_requests_total",
    "Cache requests in the backend (hit/miss).",
    ["endpoint", "result"],
)

ai_circuit_breaker_state = Gauge(
    "ai_circuit_breaker_state",
    "Circuit breaker state by provider: 0=closed, 1=open.",
    ["provider"],
)

ai_circuit_breaker_opens_total = Counter(
    "ai_circuit_breaker_opens_total",
    "Circuit breaker openings by provider.",
    ["provider", "reason"],
)

# USD per 1M input / output tokens — illustrative defaults; override with LLM_PRICING_OVERRIDES_JSON.
_DEFAULT_PRICE_PER_MILLION: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gemini-2.0-flash": (0.10, 0.40),
    "gemini-1.5-pro": (1.25, 5.00),
}
_CLOUD_PRICE_FALLBACK_PER_MILLION = (1.0, 4.0)
PRICE_PER_MILLION: dict[str, tuple[float, float]] = dict(_DEFAULT_PRICE_PER_MILLION)


def _load_pricing_overrides() -> None:
    raw = os.getenv("LLM_PRICING_OVERRIDES_JSON", "").strip()
    if not raw:
        return
    try:
        data = json.loads(raw)
        for model, v in data.items():
            PRICE_PER_MILLION[str(model)] = (float(v["input"]), float(v["output"]))
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        pass


_load_pricing_overrides()


def rough_token_estimate(text: str) -> int:
    return max(1, len(text or "") // 4)


def build_cost_estimate(
    used_p: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    output_text: str,
) -> dict[str, Any]:
    in_tok = rough_token_estimate(system_prompt) + rough_token_estimate(user_prompt)
    out_tok = rough_token_estimate(output_text)
    if used_p == "ollama":
        return {
            "estimated_input_tokens": in_tok,
            "estimated_output_tokens": out_tok,
            "estimated_cost_usd": 0.0,
            "currency": "USD",
            "pricing_input_per_million_usd": 0.0,
            "pricing_output_per_million_usd": 0.0,
            "method": "local inference; no cloud token charges in this estimate",
        }
    rates = PRICE_PER_MILLION.get(model, _CLOUD_PRICE_FALLBACK_PER_MILLION)
    pin, pout = rates
    usd = (in_tok / 1_000_000.0) * pin + (out_tok / 1_000_000.0) * pout
    return {
        "estimated_input_tokens": in_tok,
        "estimated_output_tokens": out_tok,
        "estimated_cost_usd": round(usd, 8),
        "currency": "USD",
        "pricing_input_per_million_usd": pin,
        "pricing_output_per_million_usd": pout,
        "method": "~chars/4 token heuristic + static list prices; not billing data",
    }


def record_cost_metrics(endpoint: str, provider: str, model: str, cost: dict[str, Any]) -> None:
    usd = max(0.0, float(cost["estimated_cost_usd"]))
    ai_estimated_request_cost_usd.labels(
        endpoint=endpoint,
        provider=provider,
        model=model,
    ).observe(usd)
    micro = int(round(usd * 1_000_000))
    if micro > 0:
        ai_estimated_cost_microdollars_total.labels(provider=provider, model=model).inc(micro)
    ai_estimated_input_tokens_total.labels(provider=provider, model=model).inc(
        int(cost["estimated_input_tokens"])
    )
    ai_estimated_output_tokens_total.labels(provider=provider, model=model).inc(
        int(cost["estimated_output_tokens"])
    )


def get_application_id(request: Request) -> str:
    return (request.headers.get("x-application-id") or "anonymous").strip() or "anonymous"


def _now() -> float:
    return time.time()


_budget_lock = threading.Lock()
_budget_state: dict[str, dict[str, Any]] = {}


def _budget_bucket(app_id: str) -> dict[str, Any]:
    b = _budget_state.get(app_id)
    if not b:
        b = {"window_start": _now(), "spent_micro": 0}
        _budget_state[app_id] = b
    return b


def budget_snapshot(app_id: str) -> dict[str, Any]:
    if not BUDGET_ENABLED or APP_BUDGET_USD_PER_HOUR <= 0:
        return {"enabled": False}
    with _budget_lock:
        b = _budget_bucket(app_id)
        age = _now() - float(b["window_start"])
        if age >= APP_BUDGET_WINDOW_SECONDS:
            b["window_start"] = _now()
            b["spent_micro"] = 0
        limit_micro = int(round(APP_BUDGET_USD_PER_HOUR * 1_000_000))
        remaining = max(0, limit_micro - int(b["spent_micro"]))
        return {
            "enabled": True,
            "window_seconds": APP_BUDGET_WINDOW_SECONDS,
            "limit_usd": APP_BUDGET_USD_PER_HOUR,
            "spent_usd": round(int(b["spent_micro"]) / 1_000_000.0, 6),
            "remaining_usd": round(remaining / 1_000_000.0, 6),
        }


def _preestimate_cost_usd(
    provider: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    expected_output_chars: int,
) -> float:
    out_text = "x" * max(1, int(expected_output_chars))
    cost = build_cost_estimate(provider, model, system_prompt, user_prompt, out_text)
    return float(cost["estimated_cost_usd"])


def enforce_budget_or_raise(app_id: str, endpoint: str, preestimated_cost_usd: float) -> dict[str, Any]:
    if not BUDGET_ENABLED or APP_BUDGET_USD_PER_HOUR <= 0:
        return {"enabled": False}
    limit_micro = int(round(APP_BUDGET_USD_PER_HOUR * 1_000_000))
    delta_micro = int(round(max(0.0, preestimated_cost_usd) * 1_000_000))
    with _budget_lock:
        b = _budget_bucket(app_id)
        age = _now() - float(b["window_start"])
        if age >= APP_BUDGET_WINDOW_SECONDS:
            b["window_start"] = _now()
            b["spent_micro"] = 0
        spent = int(b["spent_micro"])
        if spent + delta_micro > limit_micro:
            ai_budget_block_total.labels(application_id=app_id, endpoint=endpoint).inc()
            raise HTTPException(
                status_code=402,
                detail={
                    "error": "budget_exceeded",
                    "application_id": app_id,
                    "limit_usd": APP_BUDGET_USD_PER_HOUR,
                    "window_seconds": APP_BUDGET_WINDOW_SECONDS,
                    "spent_usd": round(spent / 1_000_000.0, 6),
                    "attempted_usd": round(delta_micro / 1_000_000.0, 6),
                },
            )
        b["spent_micro"] = spent + delta_micro
    return budget_snapshot(app_id)


def record_actual_cost_to_budget(app_id: str, actual_cost_usd: float, preestimated_cost_usd: float) -> None:
    if not BUDGET_ENABLED or APP_BUDGET_USD_PER_HOUR <= 0:
        return
    actual_micro = int(round(max(0.0, actual_cost_usd) * 1_000_000))
    pre_micro = int(round(max(0.0, preestimated_cost_usd) * 1_000_000))
    adjust = actual_micro - pre_micro
    if adjust == 0:
        return
    with _budget_lock:
        b = _budget_bucket(app_id)
        b["spent_micro"] = max(0, int(b["spent_micro"]) + adjust)


_cache_lock = threading.Lock()
_cache: dict[str, dict[str, Any]] = {}


def _cache_get(key: str) -> Optional[dict[str, Any]]:
    if not CACHE_ENABLED:
        return None
    with _cache_lock:
        v = _cache.get(key)
        if not v:
            return None
        if _now() >= float(v["expires_at"]):
            _cache.pop(key, None)
            return None
        return dict(v["payload"])


def _cache_put(key: str, payload: dict[str, Any]) -> None:
    if not CACHE_ENABLED:
        return
    with _cache_lock:
        if len(_cache) >= CACHE_MAX_ENTRIES:
            first = next(iter(_cache.keys()), None)
            if first is not None:
                _cache.pop(first, None)
        _cache[key] = {"expires_at": _now() + CACHE_TTL_SECONDS, "payload": dict(payload)}


_circuit_lock = threading.Lock()
_circuit: dict[str, dict[str, Any]] = {}


def _circuit_state(provider: str) -> dict[str, Any]:
    s = _circuit.get(provider)
    if not s:
        s = {"fail_count": 0, "open_until": 0.0}
        _circuit[provider] = s
    return s


def circuit_allows(provider: str) -> bool:
    if not CIRCUIT_ENABLED:
        return True
    with _circuit_lock:
        s = _circuit_state(provider)
        open_until = float(s["open_until"])
        is_open = _now() < open_until
        ai_circuit_breaker_state.labels(provider=provider).set(1.0 if is_open else 0.0)
        return not is_open


def circuit_on_success(provider: str) -> None:
    if not CIRCUIT_ENABLED:
        return
    with _circuit_lock:
        s = _circuit_state(provider)
        s["fail_count"] = 0
        s["open_until"] = 0.0
        ai_circuit_breaker_state.labels(provider=provider).set(0.0)


def circuit_on_failure(provider: str, reason: str) -> None:
    if not CIRCUIT_ENABLED:
        return
    with _circuit_lock:
        s = _circuit_state(provider)
        s["fail_count"] = int(s["fail_count"]) + 1
        if int(s["fail_count"]) >= CIRCUIT_FAIL_THRESHOLD:
            s["open_until"] = _now() + CIRCUIT_OPEN_SECONDS
            ai_circuit_breaker_opens_total.labels(provider=provider, reason=reason).inc()
            ai_circuit_breaker_state.labels(provider=provider).set(1.0)


def detect_guardrail(text: str) -> Optional[dict[str, str]]:
    if not GUARDRAILS_ENABLED or GUARDRAILS_MODE == "off":
        return None
    t = (text or "").lower()
    rules: list[tuple[str, list[str]]] = [
        (
            "prompt_injection",
            [
                "ignore previous",
                "ignore all previous",
                "system prompt",
                "developer message",
                "show me your instructions",
                "reveal your instructions",
            ],
        ),
        (
            "credential_exfil",
            [
                "api key",
                "openai_api_key",
                "google_api_key",
                "password",
                "token",
                "ssh key",
                "kubeconfig",
                "env var",
                "environment variable",
            ],
        ),
    ]
    for rule, needles in rules:
        if any(n in t for n in needles):
            action = "block" if GUARDRAILS_MODE == "block" else "route_to_local"
            return {"rule": rule, "action": action}
    return None


def burn_cpu(ms: int) -> None:
    if ms <= 0:
        return
    end = time.perf_counter() + (ms / 1000.0)
    x = 0.0
    while time.perf_counter() < end:
        x = (x + 1.000001) * 0.999999


def provider_configured(provider: str) -> bool:
    if provider == "ollama":
        return True
    if provider == "openai":
        # Simulated OpenAI errors need a "slot" in the failover chain without a real API call or key.
        if DEMO_SIMULATE_OPENAI_STATUS in ("429", "500", "502", "503"):
            return True
        if CLOUD_LLM_CALLS_DISABLED:
            return False
        return bool(OPENAI_API_KEY)
    if provider == "gemini":
        if CLOUD_LLM_CALLS_DISABLED:
            return False
        return bool(GOOGLE_API_KEY)
    return False


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
                detail="tier must be 'cheap' (fast/economical) or 'smart' (higher cost/quality)",
            )
        return t
    if mode == "smart":
        return "smart"
    return "cheap"


def pick_tier(
    req: "LLMParams",
    summarize_mode: Optional[str],
    *,
    translate_use_smart_default: bool,
) -> str:
    if req.tier is not None:
        return resolve_tier(None, req.tier)
    if translate_use_smart_default:
        return "smart"
    return resolve_tier(summarize_mode, None)


def resolve_provider_and_tier(
    request: Request,
    req: "LLMParams",
    *,
    summarize_mode: Optional[str] = None,
    translate_use_smart_default: bool = False,
) -> tuple[str, str, dict[str, str]]:
    """Cost-driven routing: gateway headers beat body when provider is omitted."""
    meta: dict[str, str] = {}
    if req.provider:
        p = normalize_provider(req.provider)
        tier = pick_tier(req, summarize_mode, translate_use_smart_default=translate_use_smart_default)
        meta["source"] = "request_body"
        return p, tier, meta

    cost = request.headers.get("x-cost-route", "").lower()
    pri = request.headers.get("x-priority", "").lower()
    if pri == "low" or cost == "local":
        meta["source"] = "gateway"
        meta["rule"] = "economical_local"
        return "ollama", "cheap", meta
    if pri == "high" or cost == "cloud":
        meta["source"] = "gateway"
        meta["rule"] = "premium_cloud"
        cp = CLOUD_PROVIDER_FOR_PREMIUM
        if cp not in ("openai", "gemini"):
            cp = "openai"
        return cp, "smart", meta

    p = normalize_provider(None)
    tier = pick_tier(req, summarize_mode, translate_use_smart_default=translate_use_smart_default)
    meta["source"] = "default_env"
    return p, tier, meta


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


def failover_order(primary: str) -> list[str]:
    if not FAILOVER_ENABLED:
        return [primary]
    raw = os.getenv("AI_FAILOVER_CHAIN", "openai,gemini,ollama")
    chain = [
        p.strip().lower()
        for p in raw.split(",")
        if p.strip() and p.strip().lower() in ("ollama", "openai", "gemini")
    ]
    if not chain:
        chain = ["openai", "gemini", "ollama"]
    if primary not in chain:
        out = [primary]
        for p in chain:
            if p not in out:
                out.append(p)
        return out
    i = chain.index(primary)
    return chain[i:] + chain[:i]


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
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(url, json=payload)
    except httpx.RequestError as e:
        raise LLMUpstreamError(503, f"Ollama unreachable: {e}") from e
    if resp.status_code != 200:
        if resp.status_code in (429, 500, 502, 503, 504):
            raise LLMUpstreamError(resp.status_code, resp.text[:500])
        raise HTTPException(status_code=502, detail=f"Ollama error: {resp.text[:500]}")
    data = resp.json()
    return data["message"]["content"]


async def call_openai(model: str, system_prompt: str, user_prompt: str) -> str:
    if DEMO_SIMULATE_OPENAI_STATUS in ("429", "500", "502", "503"):
        raise LLMUpstreamError(int(DEMO_SIMULATE_OPENAI_STATUS), "demo: simulated OpenAI failure")
    if CLOUD_LLM_CALLS_DISABLED:
        raise LLMUpstreamError(
            503,
            "OpenAI calls disabled (CLOUD_LLM_CALLS_DISABLED=true, zero-cost mode)",
        )
    assert_provider_configured("openai")
    _demo_enforce_quota("openai")
    url = f"{OPENAI_BASE_URL}/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}"}
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
    except httpx.RequestError as e:
        raise LLMUpstreamError(503, str(e)) from e
    if resp.status_code != 200:
        if resp.status_code in (429, 500, 502, 503, 504):
            raise LLMUpstreamError(resp.status_code, resp.text[:500])
        raise HTTPException(
            status_code=502,
            detail=f"OpenAI error ({resp.status_code}): {resp.text[:500]}",
        )
    data = resp.json()
    out = data["choices"][0]["message"]["content"]
    _demo_record_success("openai")
    return out


async def call_gemini(model: str, system_prompt: str, user_prompt: str) -> str:
    if CLOUD_LLM_CALLS_DISABLED:
        raise LLMUpstreamError(
            503,
            "Gemini calls disabled (CLOUD_LLM_CALLS_DISABLED=true, zero-cost mode)",
        )
    assert_provider_configured("gemini")
    _demo_enforce_quota("gemini")
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
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(url, params=params, json=body)
    except httpx.RequestError as e:
        raise LLMUpstreamError(503, str(e)) from e
    if resp.status_code != 200:
        if resp.status_code in (429, 500, 502, 503, 504):
            raise LLMUpstreamError(resp.status_code, resp.text[:500])
        raise HTTPException(
            status_code=502,
            detail=f"Gemini error ({resp.status_code}): {resp.text[:500]}",
        )
    data = resp.json()
    try:
        out = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as e:
        raise HTTPException(
            status_code=502,
            detail=f"Unexpected Gemini response shape: {repr(data)[:800]}",
        ) from e
    _demo_record_success("gemini")
    return out


async def generate_text_single(provider: str, model: str, system_prompt: str, user_prompt: str) -> str:
    if provider == "ollama":
        return await call_ollama(model, system_prompt, user_prompt)
    if provider == "openai":
        return await call_openai(model, system_prompt, user_prompt)
    if provider == "gemini":
        return await call_gemini(model, system_prompt, user_prompt)
    raise HTTPException(status_code=400, detail=f"unknown provider: {provider}")


async def generate_with_failover(
    primary: str,
    tier: str,
    system_prompt: str,
    user_prompt: str,
) -> tuple[str, str, str, list[str]]:
    """
    Try primary then failover chain on 429/5xx / network errors.
    Returns (text, provider_used, model_used, attempt_log).
    """
    attempt_log: list[str] = []
    last_fail: Optional[LLMUpstreamError] = None
    prev: Optional[str] = None
    for p in failover_order(primary):
        if not provider_configured(p):
            attempt_log.append(f"{p}:skipped_not_configured")
            continue
        if not circuit_allows(p):
            attempt_log.append(f"{p}:skipped_circuit_open")
            continue
        model = model_for_provider(p, tier)
        try:
            text = await generate_text_single(p, model, system_prompt, user_prompt)
            circuit_on_success(p)
            if prev is not None:
                ai_llm_failover_total.labels(from_provider=prev, to_provider=p).inc()
            return text, p, model, attempt_log
        except LLMUpstreamError as e:
            attempt_log.append(f"{p}:http_{e.status_code}")
            circuit_on_failure(p, reason=f"http_{e.status_code}")
            prev = p
            last_fail = e
            continue
        except HTTPException:
            raise
    detail = f"All providers in failover chain failed. Log: {attempt_log}"
    if last_fail:
        detail += f" Last: {last_fail.detail[:200]}"
    raise HTTPException(status_code=502, detail=detail)


class LLMParams(BaseModel):
    provider: Optional[str] = Field(
        default=None,
        description="ollama | openai | gemini. If omitted, gateway cost-routing headers apply.",
    )
    tier: Optional[str] = Field(
        default=None,
        description="'cheap' or 'smart'.",
    )


class SummarizeRequest(LLMParams):
    text: str
    mode: Optional[str] = "auto"
    max_words: Optional[int] = 50
    cpu_burn_ms: Optional[int] = 0
    structured: Optional[bool] = False
    schema_strict: Optional[bool] = True
    cache: Optional[bool] = True


class TranslateRequest(LLMParams):
    text: str
    target_language: str
    structured: Optional[bool] = False
    schema_strict: Optional[bool] = True
    cache: Optional[bool] = True


class ModerateRequest(LLMParams):
    text: str
    cache: Optional[bool] = False


class _StructuredSummary(BaseModel):
    summary: str


class _StructuredTranslation(BaseModel):
    translated_text: str


@app.get("/chat")
def chat_ui():
    """Browser UI for live demos (Kong path should include /chat)."""
    path = _STATIC_DIR / "chat.html"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="chat UI not bundled")
    return HTMLResponse(path.read_text(encoding="utf-8"))


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "default_provider": DEFAULT_PROVIDER,
        "cloud_provider_for_premium_routes": CLOUD_PROVIDER_FOR_PREMIUM,
        "failover": {
            "enabled": FAILOVER_ENABLED,
            "chain": os.getenv("AI_FAILOVER_CHAIN", "openai,gemini,ollama"),
            "demo_simulate_openai_http": DEMO_SIMULATE_OPENAI_STATUS or None,
        },
        "cloud_llm_calls_disabled": CLOUD_LLM_CALLS_DISABLED,
        "guardrails": {
            "enabled": GUARDRAILS_ENABLED,
            "mode": GUARDRAILS_MODE,
            "force_local_provider": GUARDRAILS_FORCE_LOCAL_PROVIDER,
            "force_local_tier": GUARDRAILS_FORCE_LOCAL_TIER,
        },
        "budgeting": {
            "enabled": BUDGET_ENABLED,
            "app_budget_usd_per_hour": APP_BUDGET_USD_PER_HOUR if BUDGET_ENABLED else None,
            "window_seconds": APP_BUDGET_WINDOW_SECONDS if BUDGET_ENABLED else None,
        },
        "cache": {
            "enabled": CACHE_ENABLED,
            "ttl_seconds": CACHE_TTL_SECONDS if CACHE_ENABLED else None,
            "max_entries": CACHE_MAX_ENTRIES if CACHE_ENABLED else None,
        },
        "circuit_breaker": {
            "enabled": CIRCUIT_ENABLED,
            "fail_threshold": CIRCUIT_FAIL_THRESHOLD if CIRCUIT_ENABLED else None,
            "open_seconds": CIRCUIT_OPEN_SECONDS if CIRCUIT_ENABLED else None,
        },
        "demo_cloud_quotas": _demo_quota_snapshot(),
        "ollama": {
            "cheap": MODEL_CHEAP,
            "smart": MODEL_SMART,
            "base_url": OLLAMA_BASE_URL,
        },
        "openai": {
            "cheap": OPENAI_MODEL_CHEAP,
            "smart": OPENAI_MODEL_SMART,
            "api_key_present": bool(OPENAI_API_KEY),
            "eligible_in_failover": provider_configured("openai"),
            "base_url": OPENAI_BASE_URL,
        },
        "gemini": {
            "cheap": GEMINI_MODEL_CHEAP,
            "smart": GEMINI_MODEL_SMART,
            "api_key_present": bool(GOOGLE_API_KEY),
            "eligible_in_failover": provider_configured("gemini"),
            "api_base": GEMINI_API_BASE,
        },
        "cost_estimation": {
            "heuristic": "~4 chars per token; list prices USD per 1M tokens (see app defaults)",
            "pricing_override_env": "LLM_PRICING_OVERRIDES_JSON",
            "models_priced": sorted(PRICE_PER_MILLION.keys()),
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


def observe_application_and_size(request: Request, endpoint: str, provider: str, text: str) -> None:
    app_id = get_application_id(request)
    ai_requests_per_application.labels(application_id=app_id, endpoint=endpoint).inc()
    ai_estimated_input_chars.labels(endpoint=endpoint, provider=provider).observe(len(text or ""))


@app.post("/ai/moderate")
async def moderate(req: ModerateRequest, request: Request):
    start = time.perf_counter()
    endpoint = "/ai/moderate"
    provider, tier, routing = resolve_provider_and_tier(request, req)
    app_id = get_application_id(request)

    system_prompt = (
        "Classify the following user input for safety. "
        "Return JSON with allowed:true/false and reason."
    )
    guard = detect_guardrail(req.text)
    policy: dict[str, Any] = {"enabled": GUARDRAILS_ENABLED, "decision": None}
    if guard:
        ai_guardrail_events_total.labels(action=guard["action"], rule=guard["rule"]).inc()
        policy["decision"] = guard
        if guard["action"] == "block":
            raise HTTPException(
                status_code=403,
                detail={"error": "guardrail_blocked", "rule": guard["rule"], "action": guard["action"]},
            )
        provider = GUARDRAILS_FORCE_LOCAL_PROVIDER
        tier = GUARDRAILS_FORCE_LOCAL_TIER
        routing = {**routing, "policy_override": "guardrail_route_to_local"}

    cache_info: dict[str, Any] = {"enabled": bool(req.cache and CACHE_ENABLED), "hit": False}
    cache_key = f"{endpoint}|{app_id}|{provider}|{tier}|{req.text}"
    if req.cache and CACHE_ENABLED:
        cached = _cache_get(cache_key)
        if cached:
            ai_cache_requests_total.labels(endpoint=endpoint, result="hit").inc()
            cache_info["hit"] = True
            cached["policy"] = policy
            cached["budget"] = budget_snapshot(app_id)
            cached["cache"] = cache_info
            return cached
        ai_cache_requests_total.labels(endpoint=endpoint, result="miss").inc()

    attempts: list[str] = []
    used_p = provider
    model = model_for_provider(provider, tier)
    try:
        result_text, used_p, model, attempts = await generate_with_failover(
            provider, tier, system_prompt, req.text
        )
    except HTTPException:
        raise
    except Exception:
        result_text = json.dumps({"allowed": True, "reason": "mock: LLM unavailable"})
        used_p = provider
        model = model_for_provider(provider, tier)
        attempts = []

    allowed = True
    reason = "no reason given"
    try:
        data = json.loads(result_text)
        allowed = bool(data.get("allowed", True))
        reason = str(data.get("reason", "no reason given"))
    except Exception:
        allowed = True
        reason = "LLM returned non-JSON; assuming allowed"

    decision = "allowed" if allowed else "blocked"
    ai_moderation_decisions_total.labels(decision=decision).inc()

    elapsed = time.perf_counter() - start
    record_metrics(endpoint, used_p, model, 200, elapsed)
    observe_application_and_size(request, endpoint, used_p, req.text)
    cost = build_cost_estimate(used_p, model, system_prompt, req.text, result_text)
    record_cost_metrics(endpoint, used_p, model, cost)
    payload = {
        "provider": used_p,
        "tier": tier,
        "model_used": model,
        "routing": routing,
        "failover_attempts": attempts or None,
        "allowed": allowed,
        "reason": reason,
        "policy": policy,
        "budget": budget_snapshot(app_id),
        "cache": cache_info,
        "cost_estimate": cost,
    }
    if req.cache and CACHE_ENABLED:
        _cache_put(cache_key, payload)
    return payload


@app.post("/ai/summarize")
async def summarize(req: SummarizeRequest, request: Request):
    start = time.perf_counter()
    endpoint = "/ai/summarize"
    provider, tier, routing = resolve_provider_and_tier(
        request, req, summarize_mode=req.mode, translate_use_smart_default=False
    )
    app_id = get_application_id(request)

    observe_pii_header(request)
    burn_cpu(req.cpu_burn_ms or 0)

    guard = detect_guardrail(req.text)
    policy: dict[str, Any] = {"enabled": GUARDRAILS_ENABLED, "decision": None}
    if guard:
        ai_guardrail_events_total.labels(action=guard["action"], rule=guard["rule"]).inc()
        policy["decision"] = guard
        if guard["action"] == "block":
            raise HTTPException(
                status_code=403,
                detail={"error": "guardrail_blocked", "rule": guard["rule"], "action": guard["action"]},
            )
        provider = GUARDRAILS_FORCE_LOCAL_PROVIDER
        tier = GUARDRAILS_FORCE_LOCAL_TIER
        routing = {**routing, "policy_override": "guardrail_route_to_local"}

    structured = bool(req.structured)
    if structured:
        system_prompt = (
            "You are a summarization assistant. Return ONLY valid JSON with the key 'summary'. "
            f"Keep it under {req.max_words} words. No extra keys, no markdown."
        )
    else:
        system_prompt = f"You are a summarization assistant. Summarize in {req.max_words} words."

    cache_info: dict[str, Any] = {"enabled": bool(req.cache and CACHE_ENABLED), "hit": False}
    cache_key = f"{endpoint}|{app_id}|{provider}|{tier}|{structured}|{req.max_words}|{req.text}"
    if req.cache and CACHE_ENABLED:
        cached = _cache_get(cache_key)
        if cached:
            ai_cache_requests_total.labels(endpoint=endpoint, result="hit").inc()
            cache_info["hit"] = True
            cached["policy"] = policy
            cached["budget"] = budget_snapshot(app_id)
            cached["cache"] = cache_info
            return cached
        ai_cache_requests_total.labels(endpoint=endpoint, result="miss").inc()

    model_pre = model_for_provider(provider, tier)
    expected_output_chars = int(max(10, min(2000, (req.max_words or 50) * 6)))
    pre_cost_usd = _preestimate_cost_usd(provider, model_pre, system_prompt, req.text, expected_output_chars)
    budget = enforce_budget_or_raise(app_id, endpoint, pre_cost_usd)

    try:
        result, used_p, model, attempts = await generate_with_failover(
            provider, tier, system_prompt, req.text
        )
    except HTTPException:
        raise
    except Exception:
        snippet = (req.text or "")[: max(10, min(200, (req.max_words or 50) * 6))].strip()
        result = f"[mock-summary] {snippet}"
        used_p = provider
        model = model_for_provider(provider, tier)
        attempts = []

    summary_text = result
    structured_error: Optional[str] = None
    if structured:
        try:
            data = json.loads(result)
            parsed = _StructuredSummary.model_validate(data)
            summary_text = parsed.summary
        except Exception as e:
            structured_error = f"invalid_structured_response: {type(e).__name__}"
            if req.schema_strict:
                raise HTTPException(
                    status_code=502,
                    detail={
                        "error": "schema_validation_failed",
                        "schema": "StructuredSummary",
                        "detail": structured_error,
                    },
                )

    elapsed = time.perf_counter() - start
    record_metrics(endpoint, used_p, model, 200, elapsed)
    observe_application_and_size(request, endpoint, used_p, req.text)
    cost = build_cost_estimate(used_p, model, system_prompt, req.text, summary_text)
    record_cost_metrics(endpoint, used_p, model, cost)
    record_actual_cost_to_budget(app_id, float(cost["estimated_cost_usd"]), pre_cost_usd)
    payload = {
        "provider": used_p,
        "tier": tier,
        "model_used": model,
        "routing": routing,
        "failover_attempts": attempts or None,
        "policy": policy,
        "budget": budget,
        "cache": cache_info,
        "structured": {"enabled": structured, "schema": "StructuredSummary", "error": structured_error},
        "summary": summary_text,
        "cost_estimate": cost,
    }
    if req.cache and CACHE_ENABLED:
        _cache_put(cache_key, payload)
    return payload


@app.post("/ai/translate")
async def translate(req: TranslateRequest, request: Request):
    start = time.perf_counter()
    endpoint = "/ai/translate"
    provider, tier, routing = resolve_provider_and_tier(
        request, req, summarize_mode=None, translate_use_smart_default=True
    )
    app_id = get_application_id(request)

    observe_pii_header(request)

    guard = detect_guardrail(req.text)
    policy: dict[str, Any] = {"enabled": GUARDRAILS_ENABLED, "decision": None}
    if guard:
        ai_guardrail_events_total.labels(action=guard["action"], rule=guard["rule"]).inc()
        policy["decision"] = guard
        if guard["action"] == "block":
            raise HTTPException(
                status_code=403,
                detail={"error": "guardrail_blocked", "rule": guard["rule"], "action": guard["action"]},
            )
        provider = GUARDRAILS_FORCE_LOCAL_PROVIDER
        tier = GUARDRAILS_FORCE_LOCAL_TIER
        routing = {**routing, "policy_override": "guardrail_route_to_local"}

    structured = bool(req.structured)
    if structured:
        system_prompt = (
            f"You are a translator to {req.target_language}. Return ONLY valid JSON with the key "
            "'translated_text'. No extra keys, no markdown."
        )
    else:
        system_prompt = f"You are a translator to {req.target_language}."

    cache_info: dict[str, Any] = {"enabled": bool(req.cache and CACHE_ENABLED), "hit": False}
    cache_key = f"{endpoint}|{app_id}|{provider}|{tier}|{structured}|{req.target_language}|{req.text}"
    if req.cache and CACHE_ENABLED:
        cached = _cache_get(cache_key)
        if cached:
            ai_cache_requests_total.labels(endpoint=endpoint, result="hit").inc()
            cache_info["hit"] = True
            cached["policy"] = policy
            cached["budget"] = budget_snapshot(app_id)
            cached["cache"] = cache_info
            return cached
        ai_cache_requests_total.labels(endpoint=endpoint, result="miss").inc()

    model_pre = model_for_provider(provider, tier)
    expected_output_chars = max(10, min(4000, len(req.text or "") + 200))
    pre_cost_usd = _preestimate_cost_usd(provider, model_pre, system_prompt, req.text, expected_output_chars)
    budget = enforce_budget_or_raise(app_id, endpoint, pre_cost_usd)

    try:
        result, used_p, model, attempts = await generate_with_failover(
            provider, tier, system_prompt, req.text
        )
    except HTTPException:
        raise
    except Exception:
        result = f"[mock-translation to {req.target_language}] {req.text}"
        used_p = provider
        model = model_for_provider(provider, tier)
        attempts = []

    translated_text = result
    structured_error: Optional[str] = None
    if structured:
        try:
            data = json.loads(result)
            parsed = _StructuredTranslation.model_validate(data)
            translated_text = parsed.translated_text
        except Exception as e:
            structured_error = f"invalid_structured_response: {type(e).__name__}"
            if req.schema_strict:
                raise HTTPException(
                    status_code=502,
                    detail={
                        "error": "schema_validation_failed",
                        "schema": "StructuredTranslation",
                        "detail": structured_error,
                    },
                )

    elapsed = time.perf_counter() - start
    record_metrics(endpoint, used_p, model, 200, elapsed)
    observe_application_and_size(request, endpoint, used_p, req.text)
    cost = build_cost_estimate(used_p, model, system_prompt, req.text, translated_text)
    record_cost_metrics(endpoint, used_p, model, cost)
    record_actual_cost_to_budget(app_id, float(cost["estimated_cost_usd"]), pre_cost_usd)
    payload = {
        "provider": used_p,
        "tier": tier,
        "model_used": model,
        "routing": routing,
        "failover_attempts": attempts or None,
        "policy": policy,
        "budget": budget,
        "cache": cache_info,
        "structured": {"enabled": structured, "schema": "StructuredTranslation", "error": structured_error},
        "translated_text": translated_text,
        "target_language": req.target_language,
        "cost_estimate": cost,
    }
    if req.cache and CACHE_ENABLED:
        _cache_put(cache_key, payload)
    return payload
