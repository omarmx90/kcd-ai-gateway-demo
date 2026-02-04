#!/usr/bin/env bash
set -euo pipefail

echo "📦 Installing Kong CRDs (kubectl-managed)..."
echo "🔎 Current kube context: $(kubectl config current-context)"

kubectl apply -f https://raw.githubusercontent.com/Kong/charts/main/charts/kong/crds/custom-resource-definitions.yaml

echo "✅ Kong CRDs installed."
kubectl get crd | grep konghq.com || true
