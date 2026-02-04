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
  - Demo CPU burn knob (`cpu_burn_ms`) to trigger HPA
  - Prometheus metrics (`/metrics`) with request and PII counters
- Observability stack (kube-prometheus-stack: Prometheus + Grafana)
- HPA configured for the backend
- Makefile automation for the full flow

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
- `k8s/deploy-backend.yaml`: Deployment with CPU requests/limits and `PROVIDER` env
- `k8s/service-backend.yaml`: Service exposing port 80 → container 8000
- `k8s/servicemonitor-backend.yaml`: Prometheus scrape config for the backend
- `scripts/install-observability.sh`: metrics-server + kube-prometheus-stack (Grafana on NodePort 32081)
- `scripts/install-kong.sh`: Kong installation (ServiceMonitor enabled)
- `app/app.py`: FastAPI app with `/metrics`, PII header tracking, and CPU burn

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

Happy demoing! 🎤 If you want to extend the policy (e.g., credit card/IBAN patterns) or preload more dashboards, open an issue or PR.