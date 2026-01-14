# KCD Guadalajara — AI Gateway Demo

This project demonstrates how to evolve from a traditional API Gateway to an AI Gateway on Kubernetes, enabling intelligent routing, moderation, model selection, observability and autoscaling for LLM inference workloads.

## Key Capabilities

- Semantic routing
- Prompt moderation
- Model selection (cheap vs smart)
- Cost-aware decisions
- Autoscaling (HPA)
- Observability (Prometheus + Grafana)
- Reproducible with `make all`
- Fully local using Kind + Ollama + Llama 3

## Why this matters

AI workloads introduce:
- Semantics
- Cost
- Safety
- Variability
- Non-determinism
Traditional API Gateways do not govern this behavior.  
AI Gateways enable safety, routing, governance and cost control for AI platforms.

## Stack
- Kubernetes (kind)
- Kong Gateway OSS
- FastAPI backend
- Ollama (Llama 3)
- Prometheus + Grafana
- HPA Autoscaling
- Makefile automation

## Demo Outcomes
The demo will showcase:
- Normal inference
- Smart inference
- Blocked prompts
- Stress test
- Autoscaling in action
- Live observability
---