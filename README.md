# KCD Guadalajara — AI Gateway Demo

This project shows how to turn a traditional API Gateway into an AI Gateway on Kubernetes, adding governance and insights to LLM workloads: safety, PII redaction, model selection, autoscaling, and end-to-end observability.

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
  curl/hey  Host: ai-gateway.local   +----------------------+
       +----------------------------> |   Kong Gateway OSS   |
       |                              | - Ingress Controller |
       |  http://localhost:8080       | - Prometheus plugin  |
       |                              | - PII sanitizer      |
       |                              +----------+-----------+
       |                                         |
       |                               AI Backend Service
       |                                         v
       |                              +----------+-----------+
       |                              |  FastAPI AI Backend  |
       |                              | - /ai/* endpoints    |
       |                              | - /metrics (Prom)    |
       |                              +----------------------+
       |
  http://localhost:8081  (Grafana NodePort)
```

## What’s included
- Kong Gateway OSS with:
  - Ingress controller
  - Prometheus metrics
  - PII sanitizer policy (pre-function) that redacts emails/phones/SSN in request body
- FastAPI backend with:
  - Endpoints `/ai/summarize`, `/ai/translate`, `/ai/moderate`, `/health`, `/metrics`
  - Multi-vendor LLMs: Ollama (default), OpenAI (ChatGPT API), Gemini (Google AI Studio) via JSON fields `provider` and `tier` (`cheap` | `smart`)
  - Demo CPU burn knob (`cpu_burn_ms`) to trigger HPA
  - Prometheus metrics (`/metrics`) with request and PII counters
- Observability stack (kube-prometheus-stack: Prometheus + Grafana)
- HPA configured for the backend
- Makefile automation for the full flow

## Cloud LLM keys (OpenAI + Gemini)

**Recommended (no secrets in files):** export variables in your terminal, then create the Kubernetes Secret from literals (values stay in your shell / process list only — avoid shared machines and shell logging if you paste keys).

```bash
export OPENAI_API_KEY="sk-..."           # ChatGPT API
export GOOGLE_API_KEY="..."              # Gemini (Google AI Studio); or use GEMINI_API_KEY
./scripts/create-llm-secret-from-env.sh
kubectl rollout restart deployment/ai-backend -n ai-gateway-demo
```

You can export **only one** of the two if you only need a single provider; the script adds whichever keys are set.

Equivalent **one-liner** (bash), sin script:

```bash
kubectl create secret generic ai-llm-credentials \
  --from-literal=openai-api-key="$OPENAI_API_KEY" \
  --from-literal=google-api-key="$GOOGLE_API_KEY" \
  -n ai-gateway-demo --dry-run=client -o yaml | kubectl apply -f -
```

**PowerShell** (Windows):

```powershell
$env:OPENAI_API_KEY = "sk-..."
$env:GOOGLE_API_KEY = "..."
kubectl create secret generic ai-llm-credentials `
  --from-literal=openai-api-key=$env:OPENAI_API_KEY `
  --from-literal=google-api-key=$env:GOOGLE_API_KEY `
  -n ai-gateway-demo --dry-run=client -o yaml | kubectl apply -f -
```

**Alternative:** copy `k8s/secret-llm-keys.example.yaml` to `k8s/secret-llm-keys.local.yaml` (gitignored), edit, `kubectl apply -f` that file.

Confirm readiness: `curl -s http://localhost:8080/health -H "Host: ai-gateway.local" | jq` — `openai.configured` and `gemini.configured` should be `true` after restart.

Default model IDs (override via env in `k8s/deploy-backend.yaml`): OpenAI `gpt-4o-mini` / `gpt-4o`; Gemini `gemini-2.0-flash` / `gemini-1.5-pro`.

## Prerequisites
- Docker Desktop or Docker Engine
- kind
- kubectl
- helm
- hey (load generator)

## Quickstart (one command)

```bash
make run
```

This will:
1) Build the backend Docker image  
2) Create a kind cluster with NodePorts mapped:  
   - Kong proxy NodePort 32080 → localhost:8080  
   - Grafana NodePort 32081 → localhost:8081  
3) Install Kong CRDs and Kong via Helm  
4) Deploy backend, Service, Ingress, HPA, and PII plugin  
5) Install metrics-server, Prometheus, Grafana (+ dashboards)  
6) Verify health, gateway routing, metrics and Grafana reachability

On success, you’ll see curl tips and how to fetch the Grafana password.

## Key Makefile targets

- `make run`: Full end-to-end bring-up (build → cluster → kong → app → observability → verify)
- `make verify`: Re-run the end-to-end checks (health, summarize, metrics, Grafana)
- `make load`: Generate heavy load to trigger HPA scaling
- `make status`: Show Pods/Services/Ingress in app, kong, and monitoring namespaces
- `make dashboards`: Re-apply Grafana dashboards ConfigMaps
- `make destroy`: Delete the kind cluster
- `make llm-secret`: Create/update `ai-llm-credentials` from `OPENAI_API_KEY` and/or `GOOGLE_API_KEY` (or `GEMINI_API_KEY`)

## Endpoints through the Gateway

- Health:
  ```bash
  curl -s -H "Host: ai-gateway.local" http://localhost:8080/health
  ```
- Summarize:
  ```bash
  curl -s -X POST http://localhost:8080/ai/summarize \
    -H "Host: ai-gateway.local" -H "Content-Type: application/json" \
    -d '{"text":"AI Gateways add governance to LLM workloads.","max_words":20}'
  ```
- Same call with **ChatGPT cheap vs expensive** (after configuring the secret):
  ```bash
  # Economical tier (e.g. gpt-4o-mini)
  curl -s -X POST http://localhost:8080/ai/summarize \
    -H "Host: ai-gateway.local" -H "Content-Type: application/json" \
    -d '{"provider":"openai","tier":"cheap","text":"Explain Kubernetes Operators in two sentences.","max_words":40}'
  ```
  ```bash
  # Higher tier (e.g. gpt-4o)
  curl -s -X POST http://localhost:8080/ai/summarize \
    -H "Host: ai-gateway.local" -H "Content-Type: application/json" \
    -d '{"provider":"openai","tier":"smart","text":"Explain Kubernetes Operators in two sentences.","max_words":40}'
  ```
- **Gemini** the same way: `"provider":"gemini","tier":"cheap"` or `"tier":"smart"`.
- Backward-compatible: `mode":"smart"` still maps to the smart tier when `tier` is omitted (Ollama or cloud).
- Metrics (proxied):
  ```bash
  curl -s -H "Host: ai-gateway.local" http://localhost:8080/metrics | head
  ```

## PII Sanitization demo (Kong pre-function)

The plugin (`k8s/kong-pii-sanitizer-plugin.yaml`) runs for `POST /ai/*` and:
- Redacts emails, phone numbers, and US SSNs in JSON body field `text`
- Adds headers `X-PII-REDACTIONS` and `X-PII-REDACTED`

Test:
```bash
curl -s -X POST http://localhost:8080/ai/summarize \
  -H "Host: ai-gateway.local" -H "Content-Type: application/json" \
  -d '{"text":"contact me at john.doe@example.com","max_words":20}'
```
Expected: you’ll see `[REDACTED_EMAIL]` in the summary context once routed through Kong.

Prometheus counter in the backend (`ai_pii_redactions_total`) increments by the number of redactions.

## Observability

- Grafana: `http://localhost:8081`
- Get the admin password:
  ```bash
  kubectl get secret -n monitoring kube-prometheus-stack-grafana \
    -o jsonpath='{.data.admin-password}' | base64 -d; echo
  ```
- Preloaded dashboards via ConfigMaps:
  - Kong (ID 7424 equivalent) — latency/RPS/errors
  - AI Backend — RPS by endpoint/model, PII redactions/sec, moderation decisions
  - We moved “PII Redactions / sec” to the top of the backend dashboard for demos

## HPA: make it “pop”

The backend exposes a demo knob `cpu_burn_ms` to generate CPU load per request.

Run the load:
```bash
make load
```
This uses `hey` with `-host ai-gateway.local` and sends POSTs with `cpu_burn_ms` to force CPU usage, causing HPA to scale replicas.

Check HPA and Pods:
```bash
kubectl get hpa -n ai-gateway-demo
kubectl get pods -n ai-gateway-demo
```

## Files of interest

- `Makefile`: full automation (build, cluster, kong, deploy, observability, verify, load)
- `k8s/ingress-gateway.yaml`: Kong Ingress (routes `/health`, `/metrics`, and `/ai/*`)
- `k8s/kong-pii-sanitizer-plugin.yaml`: PII redaction pre-function (Lua) policy
- `k8s/deploy-backend.yaml`: Deployment with CPU requests/limits, `PROVIDER`, and optional `ai-llm-credentials` secret refs
- `k8s/secret-llm-keys.example.yaml`: template for OpenAI + Gemini API keys (copy to `secret-llm-keys.local.yaml`, gitignored)
- `k8s/service-backend.yaml`: Service exposing port 80 → container 8000
- `k8s/servicemonitor-backend.yaml`: Prometheus scrape config for the backend
- `scripts/install-observability.sh`: metrics-server + kube-prometheus-stack (Grafana on NodePort 32081)
- `scripts/install-kong.sh`: Kong installation (ServiceMonitor enabled)
- `scripts/create-llm-secret-from-env.sh`: build the LLM API Secret from env vars (no plaintext YAML)
- `app/app.py`: FastAPI app with Ollama/OpenAI/Gemini routing, `/metrics`, PII header tracking, and CPU burn

## Troubleshooting

- Verify Kong and Ingress are up:
  ```bash
  kubectl get pods -n kong
  kubectl get ingress -n ai-gateway-demo
  ```
- If `/health` returns non-200 during startup, re-run:
  ```bash
  make verify
  ```
- If you get 404s under load from `hey`, ensure the Host header is set:
  - We use `-host ai-gateway.local` (not just `-H 'Host: ...'`) in `make load`
- If Grafana is not reachable, wait a bit:
  ```bash
  kubectl get pods -n monitoring
  ```
- If metrics don’t show in Grafana yet, check Prometheus targets:
  ```bash
  kubectl -n monitoring port-forward svc/kube-prometheus-stack-prometheus 9090:9090
  # then open http://localhost:9090/targets
  ```

## Cleanup
```bash
make destroy
```

---