# 08 — Memory and Retrieval

Status: **Approved baseline (v1.0)**

---

## 1. Three kinds of memory

Conflating these is the most common reason "AI memory" features feel unreliable. Vyomel keeps them physically separate.

| Kind | Question it answers | Storage | Lifetime |
|---|---|---|---|
| **Semantic** | "What do I know about X?" | `document_chunks` (vector + tsvector) | until source deleted |
| **Structured** | "What/who/when exactly?" | `entities`, `entity_relations`, relational tables | until forgotten |
| **Episodic** | "What happened, and when?" | `episodes` (summary + vector), `audit_log` | 1 year default |

Working memory (the context assembled for a single model call) is **derived**, never stored.

---

## 2. Context graph

The graph is what turns vague references into concrete objects.

```
   person:Advisor ──advises──► person:Me
                                  │ owns
                                  ▼
                            project:Orbit
              ┌──────────────┬───┴────┬──────────────┐
              │ belongs_to   │        │ belongs_to   │
              ▼              ▼        ▼              ▼
      document:            document: document:   application:
      architecture.md      bench.csv  README     VS Code
              │                                      │
              │ mentions                             │ used_for
              ▼                                      ▼
        entity:gRPC                             episode: "ran benchmarks
                                                          2026-08-27 14:02"
```

Population sources, in decreasing confidence:
1. **Explicit** — `memory.remember`, user corrections. Confidence 1.0.
2. **Structural** — filesystem layout, git remotes, calendar attendees, email headers. Confidence 0.9.
3. **Extracted** — entities/relations pulled from document content by an LLM pass. Confidence 0.5–0.8, and **never** used alone to authorize an action.
4. **Behavioral** — co-access patterns from the audit trail. Confidence 0.3–0.6, ranking signal only.

Every relation stores `confidence` and `evidence_ref`. Low-confidence relations may influence ranking but may not be asserted as fact in an answer without a citation.

**Salience** decays: `salience = Σ access_events · exp(-λ · age_days)`, λ tuned so a month-old untouched project drops below active ones. This is what makes "continue where I left off" resolve to the right project without asking.

---

## 3. Ingestion pipeline

```
  watch roots (configured allowlist)
        │  content hash changed?  (skip if unchanged — FR-506)
        ▼
  extract   pdf→pypdfium2 · docx→python-docx · html→trafilatura
            md/txt→raw · code→tree-sitter · csv→structured summary
        │
        ▼
  normalize   strip boilerplate, keep heading structure, page/char offsets
        │
        ▼
  chunk       structure-aware: split on heading boundaries first,
              then 512-token windows with 64-token overlap;
              code splits on function/class boundaries
        │
        ▼
  enrich      heading_path, page, char_start/end, document entity link
        │
        ▼
  embed       bge-small-en-v1.5 (384-d), batched, CPU, ~1-2k chunks/min
        │
        ▼
  index       HNSW (cosine) + tsvector GIN
        │
        ▼
  graph       upsert document entity + extracted relations
```

Design notes:
- **Chunk on structure, not on character count alone.** A chunk that spans two unrelated headings retrieves badly regardless of the embedding model.
- **Offsets are mandatory.** Citations without precise locations are unverifiable, and unverifiable citations are how agents launder hallucinations (FR-505).
- **Ingestion is incremental and idempotent**, keyed on `content_hash`. Re-running over an unchanged corpus is a no-op.
- **Ingestion never leaves the machine.** Embeddings are computed locally, which is both a privacy property and a cost property.

---

## 4. Retrieval

### 4.1 Hybrid, then fused

```
  query
    ├─► query analysis:  intent · entity mentions · time expressions · filters
    │
    ├─► vector search (HNSW, cosine, k=40)
    ├─► lexical search (tsvector / BM25, k=40)
    └─► graph expansion: entities named in the query → their linked documents
              │
              ▼
      Reciprocal Rank Fusion:  score(d) = Σ_r 1 / (60 + rank_r(d))
              │
              ▼
      filters: entity scope · recency · document type · access permission
              │
              ▼
      rerank (cross-encoder, top-20 → top-8)   [optional, M9]
              │
              ▼
      context assembly with citations, token-budgeted
```

Why hybrid rather than vector-only: personal corpora are full of exact identifiers — file names, error codes, function names, ticket IDs — where lexical search is strictly better. Vector-only retrieval on a personal corpus underperforms on precisely the queries users care most about. RRF is chosen over weighted score fusion because it needs no score normalization and no per-corpus tuning.

### 4.2 Context assembly

Budget-aware, in priority order:

1. System instruction and capability ceiling
2. Task state and prior step results (compressed)
3. Structured context-graph facts (compact triples — high value per token)
4. Retrieved chunks, deduplicated, with citations
5. Relevant episodes
6. Tool catalog (capability-filtered)

Overflow is handled by dropping from the bottom, then summarizing prior step results, and finally reducing `k`. **The system instruction and capability ceiling are never dropped** — that would be a security failure, not a quality one.

### 4.3 Trust tagging

Every context block carries provenance (`06` §5.1). Retrieved chunks from web content are `tool_untrusted` and are wrapped in delimiters marking them as data. This is applied at assembly time, so no prompt author can forget it.

---

## 5. Episodic memory

After every task, a compact record:

```json
{
  "task_id": "01J...",
  "summary": "Benchmarked Orbit gateway failover; P99 recovery 1.8s; results in bench.csv",
  "entities": ["project:Orbit", "document:bench.csv"],
  "outcome": "SUCCEEDED",
  "tools_used": ["shell.run", "fs.write_file"],
  "started_at": "2026-08-27T14:02:00Z",
  "human_interventions": 1
}
```

This powers "what did I do yesterday", supplies few-shot precedent for similar future tasks, and is the mining input for workflow learning.

---

## 6. Workflow learning (FR-901–903)

```
  audit_log  ──► normalize actions into signatures
                 sig = (tool, param_shape, target_type)   -- values stripped
        │
        ▼
  sequence mining (PrefixSpan over per-task action sequences)
        │  support >= 3 occurrences, length >= 3
        ▼
  candidate workflow
        │  generalize: values that varied across occurrences become parameters
        ▼
  propose to user  ──►  accepted?  ──► workflows table (trust_level <= L2)
                        rejected?  ──► suppression list (never re-proposed)
```

Guardrails: learned workflows never execute automatically; they are *offered*. Their trust level is capped at L2, so a learned workflow can never silently gain the ability to send email or spend money. Any L3+ step inside a learned workflow still hits its own approval gate every run.

---

## 7. Retrieval evaluation

Quality is measured, not assumed. `evals/suites/rag/`:

| Metric | Definition | Target |
|---|---|---|
| `recall@10` | fraction of queries where ≥1 gold chunk appears in top-10 | ≥ 0.85 (NFR-04) |
| `mrr@10` | mean reciprocal rank of the first gold chunk | ≥ 0.60 |
| `ndcg@10` | graded relevance | ≥ 0.70 |
| `citation_precision` | cited spans that actually support the claim (LLM-judged, human-spot-checked) | ≥ 0.90 |
| `answer_accuracy` | correctness on 100 labeled personal-corpus questions | ≥ 0.85 |
| `retrieval_latency_p95` | end-to-end retrieval | < 300 ms |

The benchmark corpus is a **synthetic-but-realistic** personal document set committed to `evals/fixtures/corpus/` (fabricated resumes, project notes, rubrics, emails, invoices). Real personal documents are never committed — the corpus must be shareable so results are reproducible by anyone, including an interviewer.

Ablations to run and record in `evals/results/`: vector-only vs lexical-only vs hybrid; with/without graph expansion; with/without reranking; chunk size 256/512/1024; embedding model comparison. **These ablation tables are the strongest evidence that the RAG work was engineered rather than assembled** — they are the artifact to show in an interview.
