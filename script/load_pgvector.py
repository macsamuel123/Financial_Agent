"""Load the CRA guide chunks (data/tfsa_store.json) into Neon pgvector.

Writes to cra_knowledge_base.documents, the table the MCP tool queries.
Run from the repository root:  python -m script.load_pgvector
"""

import json
import os

import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import execute_values

load_dotenv()

# Connection string comes from .env / Codespaces secrets. Never hardcode the password.
DATABASE_URL = os.getenv("DATABASE_URL")

STORE_PATH = "data/tfsa_store.json"
EMBEDDING_DIMS = 3072   # text-embedding-3-large, as used in app/ingest.py


def save_chunks_to_pgvector(chunks):
    dims = {len(c["embedding"]) for c in chunks}
    assert dims == {EMBEDDING_DIMS}, f"Expected {EMBEDDING_DIMS}-dim embeddings, found {dims}"

    rows = [
        (c["chunk_id"], c["source"], c["page"], c["text"], c["embedding"])
        for c in chunks
    ]

    conn = psycopg2.connect(DATABASE_URL, sslmode="require")
    try:
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
            cur.execute("CREATE SCHEMA IF NOT EXISTS cra_knowledge_base")
            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS cra_knowledge_base.documents (
                    id               SERIAL PRIMARY KEY,
                    chunk_id         TEXT UNIQUE NOT NULL,
                    source_document  TEXT NOT NULL,
                    page             INT,
                    chunk_text       TEXT NOT NULL,
                    embedding        VECTOR({EMBEDDING_DIMS}) NOT NULL
                )
            """)
            execute_values(
                cur,
                """
                INSERT INTO cra_knowledge_base.documents
                    (chunk_id, source_document, page, chunk_text, embedding)
                VALUES %s
                ON CONFLICT (chunk_id) DO UPDATE SET
                    source_document = EXCLUDED.source_document,
                    page            = EXCLUDED.page,
                    chunk_text      = EXCLUDED.chunk_text,
                    embedding       = EXCLUDED.embedding
                """,
                rows,
                template="(%s, %s, %s, %s, %s::vector)",
            )
            cur.execute("SELECT count(*) FROM cra_knowledge_base.documents")
            total = cur.fetchone()[0]
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    print(f"Upserted {len(rows)} chunks. Table now holds {total} chunks.")


if __name__ == "__main__":
    with open(STORE_PATH) as f:
        save_chunks_to_pgvector(json.load(f))