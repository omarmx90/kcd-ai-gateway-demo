import os
import json
import time
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response

app = FastAPI(title="AI Backend Demo (Ollama + Llama3)")

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
MODEL_CHEAP = os.getenv("MODEL_CHEAP", "llama3")
MODEL_SMART = os.getenv("MODEL_SMART", "llama3")
PROVIDER = os.getenv("PROVIDER", "ollama")

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


class SummarizeRequest(BaseModel):
    text: str
    mode: Optional[str] = "auto"
    max_words: Optional[int] = 50
    # Demo knob: force CPU work so HPA can scale even if LLM call is mostly I/O
    cpu_burn_ms: Optional[int] = 0


class TranslateRequest(BaseModel):
    text: str
    target_language: str


class ModerateRequest(BaseModel):
    text: str


def burn_cpu(ms: int) -> None:
    """Busy-loop for ~ms milliseconds (demo-only)."""
    if ms <= 0:
        return
    end = time.perf_counter() + (ms / 1000.0)
    x = 0.0
    while time.perf_counter() < end:
        x = (x + 1.000001) * 0.999999  # keep CPU busy


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

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(url, json=payload)
        if resp.status_code != 200:
            raise HTTPException(status_code=500, detail=f"Ollama error: {resp.text}")
        data = resp.json()
        return data["message"]["content"]


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/health")
async def health():
    return {"status": "ok", "provider": PROVIDER, "cheap": MODEL_CHEAP, "smart": MODEL_SMART}


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
    model = MODEL_CHEAP
    system_prompt = (
        "Classify the following user input for safety. "
        "Return JSON with allowed:true/false and reason."
    )
    try:
        raw = await call_ollama(model, system_prompt, req.text)
    except Exception:
        # Fallback: allow with mock reason to avoid failing demos when LLM is unavailable
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
    record_metrics(endpoint, PROVIDER, model, 200, elapsed)
    return {"allowed": allowed, "reason": reason}


@app.post("/ai/summarize")
async def summarize(req: SummarizeRequest, request: Request):
    start = time.perf_counter()
    endpoint = "/ai/summarize"
    model = MODEL_CHEAP
    if req.mode == "smart":
        model = MODEL_SMART

    observe_pii_header(request)

    # CPU burn to make autoscaling obvious
    burn = req.cpu_burn_ms or 0
    if burn > 0:
        t0 = time.time()
        while (time.time() - t0) * 1000 < burn:
            pass

    system_prompt = f"You are a summarization assistant. Summarize in {req.max_words} words."
    try:
        result = await call_ollama(model, system_prompt, req.text)
    except Exception:
        # Fallback summary so the demo keeps flowing without a running LLM
        snippet = (req.text or "")[: max(10, min(200, (req.max_words or 50) * 6))].strip()
        result = f"[mock-summary] {snippet}"

    elapsed = time.perf_counter() - start
    record_metrics(endpoint, PROVIDER, model, 200, elapsed)
    return {"provider": PROVIDER, "model_used": model, "summary": result}


@app.post("/ai/translate")
async def translate(req: TranslateRequest, request: Request):
    start = time.perf_counter()
    endpoint = "/ai/translate"
    model = MODEL_SMART

    observe_pii_header(request)

    system_prompt = f"You are a translator to {req.target_language}."
    try:
        result = await call_ollama(model, system_prompt, req.text)
    except Exception:
        result = f"[mock-translation to {req.target_language}] {req.text}"

    elapsed = time.perf_counter() - start
    record_metrics(endpoint, PROVIDER, model, 200, elapsed)
    return {"provider": PROVIDER, "translated_text": result, "target_language": req.target_language}
