# KCD Guadalajara — AI Gateway Demo

This repo shows how to evolve a **Kubernetes API gateway** into an **AI gateway**: the same operational primitives you already expect—**policy, identity, observability, and cost signals**—applied to LLM traffic.

## Contents

- [Architecture](#architecture)
- [What’s included](#whats-included)
- [Prerequisites](#prerequisites)
- [Local DNS (`ai-gateway.local`)](#local-dns-ai-gatewaylocal)
- [Quickstart](#quickstart)
- [Repository layout](#repository-layout)
- [Makefile](#makefile)
- [Scripts](#scripts)
- [Kubernetes manifests](#kubernetes-manifests)
- [Cloud LLM keys](#cloud-llm-keys-openai--gemini)
- [Cost estimate (FinOps)](#cost-estimate-not-your-real-bill)
- [Zero cloud spend](#zero-cloud-spend-bill-free-demo)
- [Keynote quotas (one OpenAI + one Gemini)](#keynote-quotas-one-openai--one-gemini)
- [**10-minute live demo (platform engineer script)**](#10-minute-live-demo-platform-engineer--architect-script)
- [Extended rehearsal checklist](#extended-rehearsal-checklist)
- [Five platform demos](#five-platform-demos-talk-track)
- [Endpoints through the gateway](#endpoints-through-the-gateway)
- [PII sanitization](#pii-sanitization-demo-kong-pre-function)
- [Observability](#observability)
- [HPA and load](#hpa-make-it-pop)
- [Troubleshooting](#troubleshooting)
- [Cleanup](#cleanup)

## Architecture

```
               +------------------------------+
               |         Grafana              |
               |  Dashboards (Kong + App)     |
               +---------------+--------------+
                               ^
                               | Prometheus scrape
                      +--------+--------+
                      |   Prometheus    |
                      +--------+--------+
                               ^
                               |
  curl / Browser Host: ai-gateway.local   +----------------------+
       +--------------------------------> |   Kong Gateway OSS   |
       |  http://localhost:8080           | - Ingress Controller |
       |  /ai/* , /chat , /health         | - Prometheus plugin  |
       |                                   | - Cost router        |
       |                                   | - PII sanitizer      |
       |                                   | - Rate limit by app  |
       |                                   +----------+-----------+
       |                                              |
       |                                   FastAPI Service
       |                                              v
       |                                   +----------+-----------+
       |                                   |  AI Backend          |
       |                                   |  /ai/* , /chat |
       |                                   |  /metrics |
       |                                   +----------------------+
       |
  http://localhost:8081  (Grafana NodePort)
```

**Platform framing:** Clients hit **one hostname**; **Kong** enforces cross-cutting policies; **Prometheus** scrapes **Kong + the app**; **Grafana** ties SLO-style views to **business signals** (e.g. per-`X-Application-Id`, heuristic cost).

## What’s included

| Layer | What |
|--------|------|
| **Gateway (Kong)** | Ingress, Prometheus plugin, **cost-router** (short text / priority → local vs cloud), **PII sanitizer** (redact + card block), **rate limit** keyed on `X-Application-Id` |
| **App (FastAPI)** | `/ai/summarize`, `/ai/translate`, `/ai/moderate`, `/health`, `/metrics`, **`/chat` UI**; multi-provider (**Ollama**, **OpenAI**, **Gemini**); failover chain; **`cost_estimate`** in JSON; Prometheus `ai_*` metrics |
| **Cluster** | **kind** + **HPA**; **kube-prometheus-stack**; **ServiceMonitor** for app `/metrics` |
| **Automation** | **Makefile** (`make run`, `make verify`, …); shell scripts under `scripts/` |

## Prerequisites

- Docker
- [kind](https://kind.sigs.k8s.io/)
- `kubectl`
- `helm`
- `curl`
- `hey` (for `make load`)
- Optional: **Ollama** on the host (`host.docker.internal:11434`) for local LLM
- Optional: **OpenAI** + **Google AI (Gemini)** API keys for cloud paths

## Local DNS (`ai-gateway.local`)

The Ingress rule uses host **`ai-gateway.local`**. Map it to **127.0.0.1** so `curl` and the browser send the correct `Host` header.

**Linux / macOS** (`/etc/hosts`):

```bash
127.0.0.1 ai-gateway.local
```

**Windows** (run Notepad *as Administrator*, edit `C:\Windows\System32\drivers\etc\hosts`):

```text
127.0.0.1   ai-gateway.local
```

Then use **`http://ai-gateway.local:8080`** for the gateway and **`http://ai-gateway.local:8080/chat`** for the chat UI. (Ports: **8080** → Kong NodePort, **8081** → Grafana.)

## Quickstart

```bash
make run
```

This builds the image, creates the kind cluster (port maps **8080** / **8081**), installs Kong, deploys the app + plugins + Ingress + HPA, installs **metrics-server**, **kube-prometheus-stack** (applies **`k8s/servicemonitor-backend.yaml`**), loads Grafana dashboards, prints status, and runs **`make verify`**.

```bash
make help # all targets
make verify # quick health check (includes /chat)
make verify-metrics # Prometheus scrape path sanity check
```

## Repository layout

| Path | Role |
|------|------|
| `app/app.py` | FastAPI: routing metadata, failover, `/metrics`, `/chat` |
| `app/static/chat.html` | Browser UI (Summarize / Translate / Moderate) |
| `app/Dockerfile` | Image build |
| `app/requirements.txt` | Python deps |
| `k8s/*.yaml` | Namespace, Deployment, Service, Ingress, HPA, KongPlugins, ServiceMonitor, secret example |
| `scripts/*.sh` | Cluster, Kong, deploy, observability, dashboards, demos, LLM secret |

## Makefile

Run **`make help`**. Common targets:

| Target | Purpose |
|--------|---------|
| `make run` | Full bring-up + verify |
| `make verify` | Context, pods, `/health`, `/ai/summarize`, `/metrics`, **`/chat`**, PII sample, in-pod metrics names, Grafana |
| `make verify-metrics` | ServiceMonitor `ai-backend`, Service port `http`, list `ai_*` from pod |
| `make status` | `kubectl get` for app, kong, monitoring |
| `make dashboards` | Re-apply Grafana ConfigMaps from `k8s/grafana/dashboards/` |
| `make observability` | metrics-server + kube-prometheus-stack + ServiceMonitor |
| `make deploy` | Apply backend + Kong plugins + ingress + HPA |
| `make load` | `hey` + `cpu_burn_ms` to stress HPA |
| `make demo-scenarios` | All five scripted scenarios |
| `make keynote-cloud` | Two cloud calls (use with quotas) |
| `make llm-secret` | Create `ai-llm-credentials` from env |
| `make grafana-pass` | Print Grafana admin password |
| `make destroy` | Delete kind cluster |

## Scripts

| Script | Purpose |
|--------|---------|
| `scripts/create-cluster.sh` | kind cluster + host ports 8080 / 8081 |
| `scripts/install-kong-crds.sh` | Kong CRDs |
| `scripts/install-kong.sh` | Kong Helm install |
| `scripts/deploy-backend.sh` | `kubectl apply` namespace, deploy, service, plugins, ingress, HPA |
| `scripts/install-observability.sh` | metrics-server patch, kube-prometheus-stack, **`servicemonitor-backend.yaml`** |
| `scripts/install-grafana-dashboards.sh` | ConfigMaps for Kong + AI backend dashboards |
| `scripts/create-llm-secret-from-env.sh` | Secret from `OPENAI_API_KEY` / `GOOGLE_API_KEY` |
| `scripts/verify-backend-metrics.sh` | ServiceMonitor + `/metrics` smoke |
| `scripts/demo-five-scenarios.sh` | Failover, cost routing, PAN 403, latency, rate limit |
| `scripts/demo-keynote-two-cloud-calls.sh` | One OpenAI + one Gemini summarize |

## Kubernetes manifests

| File | Purpose |
|------|---------|
| `namespace.yaml` | `ai-gateway-demo` |
| `deploy-backend.yaml` | Deployment: env for providers, failover, quotas, `CLOUD_LLM_CALLS_DISABLED`, etc. |
| `service-backend.yaml` | Service port **http** → 8000 |
| `ingress-gateway.yaml` | Kong Ingress: `/ai/*`, `/chat`, `/health`, `/metrics` + plugin list |
| `hpa-backend.yaml` | HPA |
| `kong-pii-sanitizer-plugin.yaml` | PII redaction + card block |
| `kong-cost-router-plugin.yaml` | Sets `X-Cost-Route` from body length / `x-priority` |
| `kong-rate-limit-app-plugin.yaml` | Rate limit by `X-Application-Id` |
| `kong-prometheus-plugin.yaml` | Kong metrics |
| `servicemonitor-backend.yaml` | Prometheus scrape (`release: kube-prometheus-stack`, ns `monitoring`) |
| `secret-llm-keys.example.yaml` | Template for keys (prefer env + script) |

## Cloud LLM keys (OpenAI + Gemini)

**Recommended:** export keys, then:

```bash
export OPENAI_API_KEY="sk-..."
export GOOGLE_API_KEY="..."
make llm-secret
kubectl rollout restart deployment/ai-backend -n ai-gateway-demo
```

**PowerShell:**

```powershell
$env:OPENAI_API_KEY = "sk-..."
$env:GOOGLE_API_KEY = "..."
kubectl create secret generic ai-llm-credentials `
  --from-literal=openai-api-key=$env:OPENAI_API_KEY `
  --from-literal=google-api-key=$env:GOOGLE_API_KEY `
  -n ai-gateway-demo --dry-run=client -o yaml | kubectl apply -f -
```

Confirm: `curl -s http://localhost:8080/health -H "Host: ai-gateway.local" | jq` → `openai.api_key_present` / `gemini.api_key_present`.

## Cost estimate (not your real bill)

Responses include **`cost_estimate`** (heuristic tokens × USD/M list prices). Metrics: `ai_estimated_request_cost_usd`, `ai_estimated_cost_microdollars_total`, token counters. Override prices with **`LLM_PRICING_OVERRIDES_JSON`** on the Deployment. See `app/app.py` defaults.

## Zero cloud spend (bill-free demo)

1. Do not mount cloud keys **or** set **`CLOUD_LLM_CALLS_DISABLED=true`** on the Deployment.
2. Run **Ollama** locally so “local” routes return real text.
3. Use **`DEMO_SIMULATE_OPENAI_STATUS=429`** to tell the failover story without calling OpenAI.

## Keynote quotas (one OpenAI + one Gemini)

On the Deployment: **`OPENAI_DEMO_QUOTA=1`**, **`GEMINI_DEMO_QUOTA=1`**. Check **`GET /health`** → `demo_cloud_quotas`. Run **`make keynote-cloud`** only when on stage. Reset with **`kubectl rollout restart deployment/ai-backend -n ai-gateway-demo`**.

---

## 10-minute live demo (platform engineer / architect script)

**Goal:** Show you can operate **LLM traffic like a platform**: policy at the edge, consistent **identity** (`X-Application-Id`), **observability**, and **FinOps** signals—without hand-waving.

**Before you start (30 s):** Cluster already up (`make run` earlier). Browser: Grafana tab + **`http://ai-gateway.local:8080/chat`**. Terminal: `kubectl` context = kind, optional `watch kubectl get pods -n ai-gateway-demo` on second screen.

| Time | What you do | What you say (short framing) |
|------|-------------|------------------------------|
| **0:00–1:00** | Point at architecture diagram (README or slide). `kubectl get pods -n ai-gateway-demo -n kong -n monitoring` (or `make status`). | “Same pattern as any API platform: **edge gateway** for policy, **stateless app** behind a Service, **metrics** scraped by Prometheus, **dashboards** for SRE and product.” |
| **1:00–2:30** | `curl -s -H "Host: ai-gateway.local" -H "X-Application-Id: keynote" http://localhost:8080/health \| jq` | “**Single health contract**: providers, failover chain, optional **cloud kill switch**, **demo quotas**—this is how you avoid surprises mid-demo.” |
| **2:30–4:30** | Open **Chat UI**. Send a **short** paragraph (Summarize). Expand JSON: `routing`, `provider`, `cost_estimate`. Then set sidebar **Priority → High** or paste a **long** text; send again. | “**Cost routing** is not in the app only: Kong sets **`X-Cost-Route`** from **priority** and **payload size**. The response still exposes **routing** so clients and SRE see *why* a path was chosen.” |
| **4:30–5:45** | Same UI or curl: message with **email**; show `[REDACTED_EMAIL]` in output. Optional one-liner: PAN-like **403** from Kong (see [PII](#pii-sanitization-demo-kong-pre-function)). | “**Compliance at the gateway**: PII never has to hit the model; **hard blocks** for high-risk patterns. That’s policy as code next to routing.” |
| **5:45–8:00** | Grafana → **AI Gateway – Backend Metrics**. Walk **requests**, **p95 latency by provider**, **PII redactions**, **requests by `application_id`**, **heuristic spend**. | “**Platform KPIs**: who burns budget (**per app id**), latency **by provider**, and **FinOps** counters—not vanity charts.” |
| **8:00–9:15** | Pick **one** closing beat: (A) `make keynote-cloud` or second cloud call with **quota=1** → show failover / 429 in JSON, **or** (B) rate-limit story: same `X-Application-Id` until **429** from Kong, **or** (C) `DEMO_SIMULATE_OPENAI_STATUS=429` + one summarize → **`failover_attempts`** in response. | “**Resilience and fairness**: failover when upstream throttles; **per-tenant rate limits** at the gateway so one team doesn’t starve the cluster.” |
| **9:15–10:00** | Mention HPA + `make load` as “after the talk” if asked. Q&A. | “**HPA** is the same Kubernetes story—LLM work is still **CPU/memory** and **queue depth** in production; here we fake pressure with **`cpu_burn_ms`** for a live scale-up.” |

**Commands cheat sheet (copy-paste):**

```bash
# Health story
curl -s -H "Host: ai-gateway.local" -H "X-Application-Id: keynote" http://localhost:8080/health | jq

# Happy path (short text → often local routing)
curl -s -X POST http://localhost:8080/ai/summarize \
  -H "Host: ai-gateway.local" -H "X-Application-Id: keynote" -H "Content-Type: application/json" \
  -d '{"text":"Hello world.","max_words":25}' | jq

# Premium path via header
curl -s -X POST http://localhost:8080/ai/summarize \
  -H "Host: ai-gateway.local" -H "X-Application-Id: keynote" -H "X-Priority: high" -H "Content-Type: application/json" \
  -d '{"text":"Short but expensive route demo.","max_words":25}' | jq
```

## Extended rehearsal checklist

- [ ] `make run` completes; `make verify` green.
- [ ] `make verify-metrics` if Grafana is empty (Prometheus target UP).
- [ ] Hosts: **`ai-gateway.local`** resolves.
- [ ] Grafana login; dashboards **Kong** + **AI Backend** load.
- [ ] Choose mode: **$0** (Ollama + `CLOUD_LLM_CALLS_DISABLED`) vs **two cloud flashes** (quotas + keys).
- [ ] Run **`make demo-scenarios`** once end-to-end; trim any step you won’t show in 10 minutes.
- [ ] Second screen / font size readable for audience.

## Five platform demos (talk track)

Send **`X-Application-Id`** on every request through Kong.

| # | Theme | What this repo does | Where to see it |
|---|-------|---------------------|-----------------|
| 1 | **Smart model fallback** | Failover chain on 429/5xx; `DEMO_SIMULATE_OPENAI_STATUS=429` for rehearsal | `failover_attempts`, Grafana `ai_llm_failover_total` |
| 2 | **Cost-driven routing** | Kong **cost-router**: length + `x-priority` → local vs cloud | `routing` in JSON; Grafana by provider |
| 3 | **PII / compliance** | Redact email/phone/SSN; **403** on card-like PAN | Chat or curl; `ai_pii_redactions_total` |
| 4 | **Local vs cloud** | Same `/ai/summarize`; `provider` in body overrides gateway | p95 latency by provider |
| 5 | **Rate limit per app** | Kong rate limit by `X-Application-Id` | HTTP 429; requests by `application_id` |

**Architecture note:** Kong targets one **Service**; **multi-vendor failover** is implemented in the **app** (common pattern). Enterprise gateways may push more upstream logic to the data plane—same tradeoffs as classic API management.

## Endpoints through the gateway

Always set **`Host: ai-gateway.local`** (or open **`http://ai-gateway.local:8080/...`** in the browser).

- **Chat UI:** `http://ai-gateway.local:8080/chat`
- **Health:** `GET /health`
- **Summarize:** `POST /ai/summarize` JSON `text`, `max_words`, optional `provider`, `tier`
- **Translate:** `POST /ai/translate`
- **Moderate:** `POST /ai/moderate`
- **Metrics:** `GET /metrics`

Examples:

```bash
curl -s -H "Host: ai-gateway.local" -H "X-Application-Id: my-app" http://localhost:8080/health

curl -s -X POST http://localhost:8080/ai/summarize \
  -H "Host: ai-gateway.local" -H "X-Application-Id: my-app" -H "Content-Type: application/json" \
  -d '{"text":"AI Gateways add governance to LLM workloads.","max_words":20}'
```

## PII sanitization demo (Kong pre-function)

Plugin: `k8s/kong-pii-sanitizer-plugin.yaml`. Redacts **`text`** JSON field; blocks card-like patterns with **403**; sets **`X-PII-REDACTIONS`**.

```bash
curl -s -X POST http://localhost:8080/ai/summarize \
  -H "Host: ai-gateway.local" -H "X-Application-Id: my-app" -H "Content-Type: application/json" \
  -d '{"text":"contact me at john.doe@example.com","max_words":20}'
```

## Observability

- **Grafana:** `http://localhost:8081` — user `admin`, password: `make grafana-pass`
- **Dashboards:** sidecar-loaded from ConfigMaps; after editing JSON: **`make dashboards`**
- **Prometheus targets:** `kubectl -n monitoring port-forward svc/kube-prometheus-stack-prometheus 9090:9090` → `http://localhost:9090/targets` (backend job **UP**)

## HPA: make it “pop”

```bash
make load
kubectl get hpa -n ai-gateway-demo
kubectl get pods -n ai-gateway-demo
```

## Troubleshooting

- **Wrong context:** `kubectl config use-context kind-ai-gateway-cluster`
- **Ingress / Kong:** `kubectl get pods -n kong`; `kubectl get ingress -n ai-gateway-demo`
- **429 from Kong:** rate limit — change `X-Application-Id` or `k8s/kong-rate-limit-app-plugin.yaml`
- **404 / wrong route:** ensure **Host** is `ai-gateway.local` (use hosts file + `http://ai-gateway.local:8080`)
- **Empty Grafana metrics:** `make observability` (applies ServiceMonitor); `make verify-metrics`; check Prometheus targets
- **Chat 404:** rebuild image (`make build` + `kind load`) so `app/static` is in the image

## Cleanup

```bash
make destroy
```

---

*KCD Guadalajara — demo repo for AI Gateway patterns on Kubernetes.*
