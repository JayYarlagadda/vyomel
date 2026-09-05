# 09 — Model Serving and Routing

Status: **M11 implemented (vLLM adapter + fixture serving bench; live GPU optional)**

---

## 1. Position

Vyomel must not be permanently welded to one hosted API. Two reasons: privacy (some payloads must never leave the machine — FR-703) and credibility (the self-hosted inference layer is a large part of this project's technical value).

```
                          ┌──────────────────┐
                          │   ModelRequest   │
                          │  purpose         │
                          │  sensitivity     │
                          │  complexity      │
                          │  context_tokens  │
                          │  latency_target  │
                          │  cost_ceiling    │
                          └────────┬─────────┘
                                   ▼
                          ┌──────────────────┐
                          │   ModelRouter    │
                          │  hard constraints│
                          │  then scoring    │
                          └────────┬─────────┘
              ┌────────────────────┼────────────────────┐
              ▼                    ▼                    ▼
     ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
     │ LocalProvider   │  │ vLLMProvider    │  │ CloudProvider   │
     │ llama.cpp/Ollama│  │ OpenAI-compat   │  │ OpenAI/Anthropic│
     │ CPU, ≤8B Q4     │  │ rented GPU      │  │ frontier models │
     │ private, free   │  │ self-hosted     │  │ strongest       │
     └─────────────────┘  └─────────────────┘  └─────────────────┘
                                   │
                          ┌────────▼─────────┐
                          │  Accounting      │  tokens · TTFT · latency · cost
                          │  Cache           │  deterministic-mode replay
                          │  Circuit breaker │  failover
                          └──────────────────┘
```

---

## 2. Provider interface

```python
class ModelProvider(Protocol):
    name: str
    is_remote: bool
    supports_tools: bool
    supports_structured_output: bool
    supports_vision: bool
    max_context: int
    cost_per_1k_prompt: Decimal
    cost_per_1k_completion: Decimal

    async def complete(self, req: ModelRequest) -> ModelResponse: ...
    async def stream(self, req: ModelRequest) -> AsyncIterator[Chunk]: ...
    async def embed(self, texts: list[str]) -> list[list[float]]: ...
    async def health(self) -> HealthStatus: ...
```

Because vLLM, Ollama, LM Studio, and OpenAI all speak the OpenAI-compatible HTTP API, a single `OpenAICompatibleProvider` with a configurable base URL covers three of the four backends. Anthropic gets a thin adapter. This is a deliberate simplification that keeps the surface small without losing capability.

---

## 3. Routing

### 3.1 Hard constraints (evaluated first, non-negotiable)

1. `sensitivity == SENSITIVE` ⇒ **local providers only**. Violation raises `PrivacyRoutingViolation`. If no local provider is healthy, the request **fails** — it does not fall back to cloud.
2. `context_tokens > provider.max_context` ⇒ provider ineligible.
3. Tool-calling or vision required but unsupported ⇒ ineligible.
4. Provider circuit breaker open ⇒ ineligible.
5. Offline mode ⇒ local only.

### 3.2 Scoring among eligible providers

```
score = w_cap · capability_fit(purpose, provider)
      + w_lat · latency_fit(latency_target, p50_latency)
      + w_cost · cost_fit(cost_ceiling, estimated_cost)
      + w_priv · privacy_bonus(is_remote, sensitivity)
```

Default weights by purpose:

| Purpose | Typical route | Rationale |
|---|---|---|
| `plan` | cloud frontier | Planning quality dominates end-to-end success; the highest-leverage place to spend. |
| `replan` | cloud frontier | Same, and it is rare. |
| `tool_select` | mid-tier | Constrained by a filtered catalog and schema validation. |
| `extract` / `classify` / `summarize` | **local** | High volume, low difficulty; the clearest cost win. |
| `verify_llm_judge` | mid-tier | Must not be the same model that produced the action (bias). |
| `embed` | **local always** | Documents never leave the machine. |
| `vision_ui` | vision-capable; local VLM when sensitive | |

The routing table is configuration (`config/models.yaml`), not code, so it can be tuned from benchmark results without a release.

### 3.3 Failover and circuit breaking

Per provider: 5 consecutive failures or a 50 % error rate over 20 calls opens the breaker for 60 s (half-open probe after). On open, the router re-scores among the remaining eligible providers — subject to the hard constraints, which are never relaxed by failover.

---

## 4. Local inference

- Runtime: `llama.cpp` server (OpenAI-compatible) or Ollama on `:11434`.
- Models: `Qwen2.5-7B-Instruct-Q4_K_M`, `Llama-3.1-8B-Instruct-Q4_K_M` (~4.5 GB resident), `Phi-3.5-mini` for the fastest classification path.
- Embeddings: `bge-small-en-v1.5` (384-d) via `sentence-transformers`, CPU, batched.
- Expected CPU throughput on this machine (8 cores, no usable GPU): roughly 8–20 tok/s for a 7B Q4 model. Adequate for classification, extraction, and short summaries; **not** adequate for planning. This is exactly why routing exists.

FR-703 and NFR-12 mean the local path must work with the network fully disabled. `tests/models/test_offline.py` asserts a complete task can run local-only.

---

## 5. vLLM (FR-707)

### 5.1 Reality

The development GPU (MX330, 2 GB, compute 6.1) cannot run vLLM — see `13-ENVIRONMENT.md` C-1. Pretending otherwise would produce a resume claim that collapses under one question.

### 5.2 Approach

1. **Adapter is real and local.** `vyomel/models/providers/vllm.py` is production code, tested against a mock OpenAI-compatible server plus a live server in the benchmark session.
2. **Deployment artifacts are real.** `infra/vllm/docker-compose.yml`, `infra/k8s/vllm-statefulset.yaml` (GPU node selector, tolerations, PVC for weights, readiness on `/health`), and a startup script with tuned `--max-model-len`, `--gpu-memory-utilization`, `--max-num-seqs`.
3. **Benchmarks are real.** A rented A10G/L4 (~$0.30–0.80/hr, 2–4 hours total) runs `evals/suites/serving/`, and results are committed to `evals/results/serving/`.
4. **Reachable from dev.** SSH tunnel: `ssh -L 8000:localhost:8000 <gpu-host>`, then `VYOMEL_VLLM_BASE_URL=http://localhost:8000/v1`.

### 5.3 Serving benchmark plan

Compare vLLM against a naive HF `transformers` baseline on identical hardware and prompts:

| Metric | Why it matters |
|---|---|
| TTFT p50 / p95 | Interactive feel |
| Inter-token latency | Streaming smoothness |
| Output tokens/sec (per request and aggregate) | Raw throughput |
| Requests/sec at concurrency 1, 4, 8, 16, 32 | Where batching pays off |
| GPU memory utilization | PagedAttention's headline claim |
| Throughput vs `--max-num-seqs` | Batching sensitivity |
| Cost per 1M tokens vs hosted API | The economic argument |

Deliverable: a table plus plots in `evals/results/serving/README.md`, with the exact commands to reproduce. The interesting finding to look for is the continuous-batching throughput curve — that is a real, defensible engineering result, not a vendor number repeated.

---

## 6. Accounting, caching, determinism

**Accounting** (FR-704): every call writes a `model_calls` row — provider, model, purpose, prompt/completion tokens, TTFT, total latency, cost, sensitivity class, cache hit. This is what makes cost-per-task and latency-breakdown analysis possible at all.

**Caching**: key = `sha256(model || canonical(messages) || tools || temperature || seed)`, stored in Redis. Enabled by default only in deterministic mode; in normal operation it is opt-in per purpose (safe for `embed`, `classify`; unsafe for anything time-sensitive).

**Deterministic mode** (FR-706): `temperature=0`, fixed `seed`, cache-first. Required for NFR-11 — evaluation runs must be reproducible, or every measured improvement is indistinguishable from sampling noise. Note honestly: identical logits are not guaranteed across provider-side model updates, so eval runs record the exact model version string and are re-baselined when it changes.

---

## 7. Prompt management

Prompts are versioned files in `vyomel/prompts/`, not inline strings:

```
vyomel/prompts/
  planner/decompose.v3.jinja
  planner/replan.v2.jinja
  verify/llm_judge.v1.jinja
  memory/extract_entities.v2.jinja
  tools/select.v1.jinja
```

Each has a front-matter block with `version`, `purpose`, `expected_output_schema`, and `changelog`. The prompt hash is recorded on every `model_calls` row, so a change in task success rate can be attributed to a specific prompt revision. Prompt changes are evaluated exactly like code changes: run `evals/suites/agent/` before and after and compare.
