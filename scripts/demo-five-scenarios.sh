#!/usr/bin/env bash
# Five narrative demos for the AI Gateway talk. Requires cluster + Kong from `make run`.
# For a $0 run: set CLOUD_LLM_CALLS_DISABLED=true on the backend and rely on Ollama; step 4's OpenAI timing may return errors unless keys + flag off.
# Usage: ./scripts/demo-five-scenarios.sh
set -euo pipefail

H="Host: ai-gateway.local"
BASE="http://localhost:8080"
APP="X-Application-Id: demo-script"
NS="ai-gateway-demo"

echo "=== 1) Smart model fallback (platform resilience) ==="
echo "Simulating OpenAI 429 in the backend; with Gemini keys configured you should see failover to Gemini/Ollama in JSON (failover_attempts / provider)."
kubectl set env deployment/ai-backend -n "${NS}" DEMO_SIMULATE_OPENAI_STATUS=429 >/dev/null
kubectl rollout status deployment/ai-backend -n "${NS}" --timeout=120s
curl -sS -X POST "${BASE}/ai/summarize" -H "${H}" -H "${APP}" -H "Content-Type: application/json" \
  -d '{"provider":"openai","tier":"cheap","text":"Hello from failover demo.","max_words":15}' \
  | { command -v jq >/dev/null && jq . || cat; }
kubectl set env deployment/ai-backend -n "${NS}" DEMO_SIMULATE_OPENAI_STATUS- >/dev/null
kubectl rollout status deployment/ai-backend -n "${NS}" --timeout=120s
echo ""

echo "=== 2) Cost-driven routing (FinOps) ==="
echo "Short text (<200 chars) without provider: gateway sets X-Cost-Route local -> low-cost Ollama."
curl -sS -X POST "${BASE}/ai/summarize" -H "${H}" -H "${APP}" -H "Content-Type: application/json" \
  -d '{"text":"classify: ok","max_words":10}' \
  | { command -v jq >/dev/null && jq '.routing, .provider, .model_used' || cat; }
echo "Long text (>200 chars) without provider: X-Cost-Route cloud -> premium cloud model."
LONG=""
for ((i = 0; i < 220; i++)); do LONG+="x"; done
curl -sS -X POST "${BASE}/ai/summarize" -H "${H}" -H "${APP}" -H "Content-Type: application/json" \
  -d "{\"text\":\"${LONG}\",\"max_words\":10}" \
  | { command -v jq >/dev/null && jq '.routing, .provider, .tier' || cat; }
echo "Or force premium with x-priority: high (short text)."
curl -sS -X POST "${BASE}/ai/summarize" -H "${H}" -H "${APP}" -H "Content-Type: application/json" -H "x-priority: high" \
  -d '{"text":"short but high priority","max_words":10}' \
  | { command -v jq >/dev/null && jq '.routing, .provider, .tier' || cat; }
echo ""

echo "=== 3) PII / compliance (card-number block) ==="
code=$(curl -sS -o /tmp/pii_block.json -w "%{http_code}" -X POST "${BASE}/ai/summarize" -H "${H}" -H "${APP}" -H "Content-Type: application/json" \
  -d '{"text":"my card is 4532-1234-5678-9010 please store it","max_words":10}' || true)
echo "HTTP ${code}"
cat /tmp/pii_block.json; echo
echo ""

echo "=== 4) Local vs cloud (latency / data sovereignty) ==="
echo "Compare total time; in Grafana open the p95-by-provider panel (Ollama vs OpenAI/Gemini)."
echo -n "Ollama (local): "
curl -sS -o /dev/null -w "%{time_total}s\n" -X POST "${BASE}/ai/summarize" -H "${H}" -H "${APP}" -H "Content-Type: application/json" \
  -d '{"provider":"ollama","tier":"cheap","text":"ping","max_words":5}'
echo -n "OpenAI (cloud, if API key is set): "
curl -sS -o /dev/null -w "%{time_total}s\n" -X POST "${BASE}/ai/summarize" -H "${H}" -H "${APP}" -H "Content-Type: application/json" \
  -d '{"provider":"openai","tier":"cheap","text":"ping","max_words":5}' || true
echo ""

echo "=== 5) Rate limit by Application ID (operations) ==="
echo "Lowering limit to 8 req/min for the demo; then restoring defaults."
kubectl patch kongplugin rate-limit-by-app -n "${NS}" --type merge -p '{"config":{"minute":8,"hour":10000}}' >/dev/null
sleep 2
APP_RL="X-Application-Id: team-runaway"
echo "First requests return 200, then Kong returns 429:"
for i in $(seq 1 12); do
  c=$(curl -sS -o /dev/null -w "%{http_code}" -X POST "${BASE}/ai/summarize" -H "${H}" -H "${APP_RL}" -H "Content-Type: application/json" \
    -d "{\"text\":\"rate demo $i\",\"max_words\":5}" || echo "000")
  echo "  req $i -> HTTP $c"
done
kubectl patch kongplugin rate-limit-by-app -n "${NS}" --type merge -p '{"config":{"minute":60,"hour":2000}}' >/dev/null
echo "Limit restored to 60/min."
echo ""
echo "Done. Check Grafana dashboard \"AI Gateway - Backend Metrics\" (failover, p95 by provider, requests by application_id)."
