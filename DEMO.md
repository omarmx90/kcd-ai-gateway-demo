# DEMO — KCD Guadalajara (AI Gateway)

This is a **stage-ready demo script** showing how a classic **Kubernetes API Gateway** evolves into an **AI Gateway**: *edge policy*, *multi-tenancy*, *resilience*, *observability*, and *FinOps signals* for LLM traffic.

## Quick setup (before you go on stage)

- Keep these tabs open:
  - **Chat UI**: `http://ai-gateway.local:8080/chat`
  - **Grafana**: `http://localhost:8081` (password: `make grafana-pass`)
  - **Repo**: `README.md` + 2–4 key files (below)
- In a terminal, ready to run:

```bash
make run
make verify
```

If you're doing a **$0 / bill-free** run:
- Set `CLOUD_LLM_CALLS_DISABLED=true` in `k8s/deploy-backend.yaml` (or via env) and rely on local Ollama.

## Files you will show (the “heroes”)

- **Gateway routing + PII policy**: `k8s/kong-ai-gateway-policy-plugin.yaml`
- **Per-tenant rate limit**: `k8s/kong-rate-limit-app-plugin.yaml`
- **Ingress + plugins applied**: `k8s/ingress-gateway.yaml`
- **Backend (guardrails, budgets, cache, circuit breaker, structured)**: `app/app.py`
- **Chat UI (toggles to trigger scenarios)**: `app/static/chat.html`
- **Dashboards**: `k8s/grafana/dashboards/ai-backend.json` and `k8s/grafana/dashboards/kong.json`
- **Scenario scripts**: `scripts/demo-five-scenarios.sh` and `scripts/demo-keynote-two-cloud-calls.sh`

## Demo (10–12 min) — Suggested talk track

### 0) Framing (30–60s)
**What you say**: “This is not a chatbot demo. This is **platform operations** for LLM traffic: policy, per-app identity, metrics, cost signals, and resilience… on Kubernetes.”

**What you show**:
- `README.md` (architecture + capabilities list)

### 1) One host, many policies (60–90s)
**What you do**:
- Open the Chat UI and show everything goes through `ai-gateway.local`.
- Optional: call `GET /health` to show the operational contract:

```bash
curl -s -H "Host: ai-gateway.local" -H "X-Application-Id: keynote" http://localhost:8080/health | jq
```

**File to show (10s)**:
- `k8s/ingress-gateway.yaml` (routes `/ai/*`, `/chat`, `/metrics` + plugins)

### 2) Cost-driven routing (FinOps) — “local vs cloud” (60–90s)
**What you do (Chat UI)**:
- Set `Gateway priority` = **Auto** and send a short text → it should go **local** (Ollama) if available.
- Set `Gateway priority` = **High** and send a short text → it forces a premium route (cloud) if enabled.

**What you say**: “The app doesn’t decide alone: the gateway sets `X-Cost-Route` based on priority/size; the app honors it and returns `routing` for observability.”

**Files to show**:
- `k8s/kong-ai-gateway-policy-plugin.yaml` (function `cost_router()`)
- `app/app.py` (function `resolve_provider_and_tier()` and the `routing` field in responses)

### 3) PII sanitization (email redaction) (60s)
**What you do (Chat UI)**:
- Mode **Summarize**
- Paste: `Contact me at john.doe@example.com for the follow-up.`
- Observe the PII is redacted before the model sees it.

**What you say**: “This is edge governance: PII doesn’t have to reach the model. Centralized policy, consistent across services.”

**Files to show**:
- `k8s/kong-ai-gateway-policy-plugin.yaml` (function `pii_sanitize()` and email/phone/ssn redaction patterns)

### 4) PII hard-block (PAN → 403) (45–60s)
**What you do**:
- Paste a PAN-like string: `My card number is 4532-1234-5678-9010 please store it`
- It should return **403** from Kong with a policy JSON.

**Files to show**:
- `k8s/kong-ai-gateway-policy-plugin.yaml` (card-like pattern block)

### 5) Guardrails “wow”: prompt injection / exfil (60–90s)
**What you do (Chat UI)**:
- Set `Guardrail preset` = **Prompt injection**
- Send any text
- Look for the chip: `policy: prompt_injection · route_to_local|block`

Then:
- Set `Guardrail preset` = **Exfiltrate secrets**
- Send again

**What you say**: “We’re not ‘solving security’ with a one-liner: we are **enforcing policy**. In safe mode we route to local or block, and we measure it.”

**Files to show**:
- `app/app.py`:
  - `detect_guardrail()`
  - `AI_GUARDRAILS_*` config
- `app/static/chat.html` (`Guardrail preset` selector)

### 6) Cache “latency wow” (45–60s)
**What you do**:
- Set `Backend cache` = **On**
- Send a prompt
- Send the **exact same** prompt again
- You should see `cache: hit` and a faster response

**Files to show**:
- `app/app.py` (`_cache_get/_cache_put`, metric `ai_cache_requests_total`)

### 7) Budget per tenant (noisy neighbor control) (60–90s)
**What you do (Chat UI)**:
- Set `X-Application-Id` = `team-runaway`
- Send multiple “expensive” prompts (e.g. `Gateway priority = High` or long text)
- When you exceed the budget, the API returns **402** `budget_exceeded`

**What you say**: “Just like rate limiting, now we also enforce *budget* (FinOps). Not hand-wavy: it’s enforced and observable per tenant.”

**Files to show**:
- `app/app.py` (`enforce_budget_or_raise`, `budget_snapshot`, `AI_APP_BUDGET_USD_PER_HOUR`)
- `k8s/deploy-backend.yaml` (optional: show env-based configuration)

### 8) Circuit breaker + failover (60–90s)
**What you do**:
- If you have cloud keys, you can trigger upstream errors (or in rehearsal use the script that simulates 429).
- Safe alternative (script):

```bash
./scripts/demo-five-scenarios.sh
```

**What to look for**:
- `failover_attempts` includes `skipped_circuit_open` when the breaker opens

**Files to show**:
- `app/app.py`:
  - `generate_with_failover()` (integrates the circuit breaker)
  - `AI_CIRCUIT_BREAKER_*`
- `scripts/demo-five-scenarios.sh` (scenario 1: failover)

### 9) Structured responses (schema) (45–60s)
**What you do (Chat UI)**:
- Set `Structured response (schema)` = **On (strict)**
- Run summarize/translate
- In the response you’ll see `structured: { enabled: true, schema: ... }`

**What you say**: “This is quality control: if downstream needs JSON, we validate the shape early; if it fails, we can fail or do best-effort.”

**Files to show**:
- `app/app.py` (`_StructuredSummary`, `_StructuredTranslation`, flags `structured/schema_strict`)
- `app/static/chat.html` (structured selector)

### 10) Observabilidad (60–90s)
**What you do**:
- Open Grafana dashboard **AI Gateway - Backend Metrics**:
  - requests/s, p95 latency
  - failover events
  - per-app traffic
  - estimated spend
  - (new) guardrails/budget/cache/circuit

**Files to show**:
- `k8s/grafana/dashboards/ai-backend.json`
- `k8s/servicemonitor-backend.yaml` (if someone asks “how does Prometheus scrape it?”)

### 11) Rate limit por tenant (opcional cierre, 45–60s)
### 11) Rate limit per tenant (optional close, 45–60s)
**What you do**:
- Keep `X-Application-Id` constant and send quickly until Kong returns **429**

**File to show**:
- `k8s/kong-rate-limit-app-plugin.yaml`

## Cheat sheet (copy/paste into the Chat UI)

- **Cost routing (short)**: `classify: ok`
- **Cost routing (long)**: paste 300+ chars (a long paragraph)
- **PII email**: `Contact me at john.doe@example.com for the follow-up.`
- **PII PAN block**: `My card number is 4532-1234-5678-9010 please store it`
- **Guardrail (prompt injection)**: use the “Prompt injection” preset
- **Guardrail (exfil)**: use the “Exfiltrate secrets” preset
- **Cache hit**: send the same prompt twice

## Rehearsal checklist (2 min)

- `make verify` is green (including `/chat`)
- Grafana dashboards load
- If running “$0”: `CLOUD_LLM_CALLS_DISABLED=true` + Ollama running locally
- If running “2 cloud calls”: use `scripts/demo-keynote-two-cloud-calls.sh` with quotas `1 + 1`

