"""Generate the synthetic RAG benchmark corpus and labeled questions.

Each document embeds a unique AXQ token so recall@k is measurable without
committing real personal data. Re-run to regenerate; committed output lives
under evals/fixtures/.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "evals" / "fixtures" / "corpus"
QUESTIONS = ROOT / "evals" / "fixtures" / "rag" / "questions.jsonl"

TOPICS = (
    ("orbit", "gateway retry policy", "gRPC mutual TLS"),
    ("nebula", "batch scheduler", "Kubernetes autoscaling"),
    ("atlas", "invoice reconciliation", "payment provider webhook"),
    ("resume", "backend role", "distributed systems interview"),
    ("cs151", "grading rubric", "late submission policy"),
)


def main() -> None:
    CORPUS.mkdir(parents=True, exist_ok=True)
    QUESTIONS.parent.mkdir(parents=True, exist_ok=True)

    question_rows: list[dict[str, object]] = []
    doc_count = 0
    for topic_index, (project, focus, detail) in enumerate(TOPICS):
        project_dir = CORPUS / project
        project_dir.mkdir(parents=True, exist_ok=True)
        for doc_index in range(20):
            token = f"AXQ{topic_index:02d}{doc_index:03d}"
            rel_path = f"{project}/note-{doc_index:02d}.md"
            path = project_dir / f"note-{doc_index:02d}.md"
            body = (
                f"# {project.title()} note {doc_index}\n\n"
                f"Unique token {token}.\n\n"
                f"## Focus\n\n{focus} for {project}: {detail}.\n\n"
                f"## Detail\n\n"
                f"Document {doc_index} discusses {focus} with emphasis on {detail}. "
                f"When searching, look for {token}.\n"
            )
            path.write_text(body, encoding="utf-8")
            doc_count += 1
            question_rows.append(
                {
                    "query": token,
                    "gold_paths": [rel_path],
                    "category": "exact_identifier",
                }
            )
            if doc_index % 4 == 0:
                question_rows.append(
                    {
                        "query": f"{focus} {project}",
                        "gold_paths": [rel_path],
                        "category": "factual",
                    }
                )

    QUESTIONS.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in question_rows) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {doc_count} documents and {len(question_rows)} questions")


if __name__ == "__main__":
    main()
