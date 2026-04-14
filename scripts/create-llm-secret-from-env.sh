#!/usr/bin/env bash
# Create or update ai-llm-credentials from environment variables (no plain-text YAML).
# Usage (bash/Git Bash):
#   export OPENAI_API_KEY="sk-..."
#   export GOOGLE_API_KEY="..."   # Gemini (Google AI Studio); GEMINI_API_KEY also works
#   ./scripts/create-llm-secret-from-env.sh
#   kubectl rollout restart deployment/ai-backend -n ai-gateway-demo
set -euo pipefail

NAMESPACE="${NAMESPACE:-ai-gateway-demo}"
SECRET_NAME="${SECRET_NAME:-ai-llm-credentials}"

args=()
if [[ -n "${OPENAI_API_KEY:-}" ]]; then
  args+=(--from-literal=openai-api-key="${OPENAI_API_KEY}")
fi
gemini_key="${GOOGLE_API_KEY:-${GEMINI_API_KEY:-}}"
if [[ -n "${gemini_key}" ]]; then
  args+=(--from-literal=google-api-key="${gemini_key}")
fi

if [[ ${#args[@]} -eq 0 ]]; then
  echo "Set at least one of: OPENAI_API_KEY, GOOGLE_API_KEY (or GEMINI_API_KEY for Gemini)." >&2
  exit 1
fi

kubectl get ns "${NAMESPACE}" >/dev/null 2>&1 || kubectl apply -f k8s/namespace.yaml

kubectl create secret generic "${SECRET_NAME}" "${args[@]}" \
  -n "${NAMESPACE}" --dry-run=client -o yaml | kubectl apply -f -

echo "Secret ${SECRET_NAME} applied in ${NAMESPACE}."
echo "Restart backend: kubectl rollout restart deployment/ai-backend -n ${NAMESPACE}"
