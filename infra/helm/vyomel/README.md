# Vyomel Helm chart

Control-plane deployment for API, workers, scheduler, Postgres, Redis, and optional vLLM.
Desktop actuation stays on the host via `vyomel agent` (ADR-0009).

## Install (kind)

```bash
kind create cluster --name vyomel
docker build -t vyomel:0.1.0 .
kind load docker-image vyomel:0.1.0 --name vyomel
helm upgrade --install vyomel infra/helm/vyomel \
  -f infra/helm/vyomel/values-kind.yaml \
  --set image.repository=vyomel --set image.tag=0.1.0 --set image.pullPolicy=Never
kubectl rollout status deploy/vyomel-api
curl http://127.0.0.1:8080/healthz   # after port-forward
```

## Failover behavior

| Component | Failure | Recovery |
|---|---|---|
| API pod | crash / eviction | kubelet restarts; clients reconnect; local agents re-advertise |
| Scheduler pod (non-leader) | crash | no effect — follower was idle |
| Scheduler pod (leader) | crash | Redis lock TTL expires; another replica acquires and recovers orphans |
| Worker pod | crash mid-action | lease expires; reaper returns action to READY; another worker claims |
| Redis flush | stream lost | scheduler `recover()` republishes DISPATCHED orphans from Postgres |
| Postgres restart | brief outage | pods crashloop until ready; durable state survives PVC |

## HPA

`worker.hpa` targets Prometheus metric `vyomel_queue_depth` (Pods averageValue) plus CPU.
kind profile disables HPA (`values-kind.yaml`) because the metrics adapter is not installed.
