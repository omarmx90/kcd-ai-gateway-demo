#!/usr/bin/env bash
# Two intentional cloud calls for a keynote (OpenAI once, Gemini once).
# Prerequisites: Secret with keys, CLOUD_LLM_CALLS_DISABLED=false, and on the Deployment:
#   OPENAI_DEMO_QUOTA=1
#   GEMINI_DEMO_QUOTA=1
# Reset counters with: kubectl rollout restart deployment/ai-backend -n ai-gateway-demo
set -euo pipefail

H="Host: ai-gateway.local"
BASE="http://localhost:8080"
APP="X-Application-Id: keynote-cloud"

echo "1) OpenAI (consumes one successful OpenAI completion if quota allows):"
curl -sS -X POST "${BASE}/ai/summarize" -H "${H}" -H "${APP}" -H "Content-Type: application/json" \
  -d '{"provider":"openai","tier":"cheap","text":"Say hello in one short sentence for a live demo.","max_words":20}' \
  | { command -v jq >/dev/null && jq . || cat; }

echo ""
echo "2) Gemini (consumes one successful Gemini completion if quota allows):"
curl -sS -X POST "${BASE}/ai/summarize" -H "${H}" -H "${APP}" -H "Content-Type: application/json" \
  -d '{"provider":"gemini","tier":"cheap","text":"Say hello in one short sentence for a live demo.","max_words":20}' \
  | { command -v jq >/dev/null && jq . || cat; }

echo ""
echo "3) Health (check demo_cloud_quotas):"
curl -sS -H "${H}" -H "${APP}" "${BASE}/health" | { command -v jq >/dev/null && jq '.demo_cloud_quotas' || cat; }
