# M4 RAG recall — 2026-09-02

Synthetic corpus: 100 markdown notes across 5 topic folders (`evals/fixtures/corpus/`).
125 labeled questions (`evals/fixtures/rag/questions.jsonl`).

**Embedder:** hashing 384-d (test/dev default). bge-small-en-v1.5 is wired via
`VYOMEL_EMBEDDING_BACKEND=bge` and the `[memory]` extra; quality numbers below
use hashing because the corpus is token-heavy by design.

## recall@10 (NFR-04 target ≥ 0.85)

| strategy | recall@10 |
|---|---:|
| hybrid (RRF k=60) | **0.928** |
| lexical only | 0.920 |
| vector only | 0.152 |

Hybrid fusion is required on this corpus when using the hashing embedder; lexical
retrieval carries almost all signal. Re-run with bge after `pip install -e ".[memory]"`.

## Reproduce

```powershell
python evals/suites/rag/recall.py --backend hashing --strategy hybrid --k 10
python evals/suites/rag/recall.py --backend hashing --strategy vector --k 10
python evals/suites/rag/recall.py --backend hashing --strategy lexical --k 10
```
