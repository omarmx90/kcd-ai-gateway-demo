import os
import json
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="AI Backend Demo (Ollama + Llama3)")

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
MODEL_CHEAP = os.getenv("MODEL_CHEAP", "llama3")
MODEL_SMART = os.getenv("MODEL_SMART", "llama3")


class SummarizeRequest(BaseModel):
    text: str
    mode: Optional[str] = "auto"
    max_words: Optional[int] = 50


class TranslateRequest(BaseModel):
    text: str
    target_language: str


class ModerateRequest(BaseModel):
    text: str


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


@app.get("/health")
async def health():
    return {"status": "ok", "cheap": MODEL_CHEAP, "smart": MODEL_SMART}


@app.post("/ai/moderate")
async def moderate(req: ModerateRequest):
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

    return {"allowed": allowed, "reason": reason}


@app.post("/ai/summarize")
async def summarize(req: SummarizeRequest):
    model = MODEL_CHEAP

    if req.mode == "smart":
        model = MODEL_SMART

    system_prompt = (
        f"You are a summarization assistant. Summarize in {req.max_words} words."
    )
    result = await call_ollama(model, system_prompt, req.text)
    return {"model_used": model, "summary": result}


@app.post("/ai/translate")
async def translate(req: TranslateRequest):
    system_prompt = f"You are a translator to {req.target_language}."
    result = await call_ollama(MODEL_SMART, system_prompt, req.text)
    return {"translated_text": result, "target_language": req.target_language}
