-- Extensions Vyomel depends on. Runs once, on first cluster initialization.
-- Schema objects themselves are owned by Alembic, not by this file.

CREATE EXTENSION IF NOT EXISTS vector;      -- pgvector: embeddings + HNSW
CREATE EXTENSION IF NOT EXISTS pg_trgm;     -- trigram matching for fuzzy entity/alias lookup
CREATE EXTENSION IF NOT EXISTS btree_gin;   -- composite GIN indexes over scalar + tsvector
