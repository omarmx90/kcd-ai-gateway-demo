import os
import json
import time
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response

app = FastAPI(title="AI Backend Demo (Ollama + Llama3)")

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
MODEL_CHEAP = os.getenv("MODEL_CHEAP", "llama3")
MODEL_SMART = os.getenv("MODEL_SMART", "llama3")

REQS = Counter(
    "ai_backend_requests_total",
    "Total number of requests",
    ["endpoint", "status"],
)
LAT = Histogram(
    "ai_backend_request_latency_seconds",
    "Request latency in seconds",
    ["endpoint"],
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
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/health")
async def health():
    return {"status": "ok", "cheap": MODEL_CHEAP, "smart": MODEL_SMART}


@app.post("/ai/moderate")
async def moderate(req: ModerateRequest):
    endpoint = "/ai/moderate"
    with LAT.labels(endpoint=endpoint).time():
        system_prompt = (
            "Classify the following user input for safety. "
            "Return JSON with allowed:true/false and reason."
        )
        user_prompt = req.text
        raw = await call_ollama(MODEL_CHEAP, system_prompt, user_prompt)

        try:
            data = json.loads(raw)
            allowed = data.get("allowed", True)
            reason = data.get("reason", "no reason given")
        except Exception:
            allowed = True
            reason = "LLM returned non-JSON; assuming allowed"

        REQS.labels(endpoint=endpoint, status="200").inc()
        return {"allowed": allowed, "reason": reason}


@app.post("/ai/summarize")
async def summarize(req: SummarizeRequest):
    endpoint = "/ai/summarize"
    with LAT.labels(endpoint=endpoint).time():
        burn_cpu(req.cpu_burn_ms or 0)

        model = MODEL_CHEAP
        if req.mode == "smart":
            model = MODEL_SMART

        system_prompt = f"You are a summarization assistant. Summarize in {req.max_words} words."
        result = await call_ollama(model, system_prompt, req.text)

        REQS.labels(endpoint=endpoint, status="200").inc()
        return {"model_used": model, "summary": result}


@app.post("/ai/translate")
async def translate(req: TranslateRequest):
    endpoint = "/ai/translate"
    with LAT.labels(endpoint=endpoint).time():
        system_prompt = f"You are a translator to {req.target_language}."
        result = await call_ollama(MODEL_SMART, system_prompt, req.text)

        REQS.labels(endpoint=endpoint, status="200").inc()
        return {"translated_text": result, "target_language": req.target_language}
