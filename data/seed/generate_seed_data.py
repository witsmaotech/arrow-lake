"""Generate seed datasets for testing and demos.

Creates:
- data/seed/users.parquet  — 1000 rows
- data/seed/documents.jsonl — 500 rows

Usage:
    uv run python data/seed/generate_seed_data.py
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

SEED_DIR = Path(__file__).parent


def generate_users(n: int = 1000) -> pa.Table:
    """Generate a users table with realistic data."""
    rng = random.Random(42)
    first_names = ["Alice", "Bob", "Carol", "Dave", "Eve", "Frank", "Grace", "Hank"]
    last_names = ["Smith", "Jones", "Lee", "Wilson", "Brown", "Davis", "Miller", "Taylor"]
    departments = ["engineering", "product", "design", "marketing", "sales", "hr"]
    cities = ["Beijing", "Shanghai", "Shenzhen", "Hangzhou", "Chengdu", "Tokyo", "Seoul"]

    ids = list(range(1, n + 1))
    names = [f"{rng.choice(first_names)} {rng.choice(last_names)}" for _ in range(n)]
    ages = [rng.randint(22, 65) for _ in range(n)]
    dept = [rng.choice(departments) for _ in range(n)]
    city = [rng.choice(cities) for _ in range(n)]
    salary = [round(rng.uniform(5000, 50000), 2) for _ in range(n)]

    return pa.table(
        {
            "id": ids,
            "name": names,
            "age": ages,
            "department": dept,
            "city": city,
            "salary": salary,
        }
    )


def generate_documents(n: int = 500) -> list[dict[str, object]]:
    """Generate JSONL documents with metadata."""
    rng = random.Random(42)
    categories = ["tech", "finance", "health", "science", "business"]
    langs = ["zh", "en", "ja", "ko"]
    sources = ["web", "api", "manual", "export"]

    docs = []
    for i in range(1, n + 1):
        docs.append(
            {
                "id": i,
                "title": f"Document {i}: {' '.join(rng.choice('abcdefg') for _ in range(5)).title()}",
                "category": rng.choice(categories),
                "language": rng.choice(langs),
                "source": rng.choice(sources),
                "word_count": rng.randint(100, 10000),
                "created_at": f"2025-01-{rng.randint(1, 28):02d}",
            }
        )

    return docs


def main() -> None:
    n_users = int(sys.argv[1]) if len(sys.argv) > 1 else 1000
    n_docs = int(sys.argv[2]) if len(sys.argv) > 2 else 500

    SEED_DIR.mkdir(parents=True, exist_ok=True)

    # Generate users.parquet
    users = generate_users(n_users)
    pq.write_table(users, str(SEED_DIR / "users.parquet"))
    print(f"Generated {SEED_DIR / 'users.parquet'} ({users.num_rows} rows)")

    # Generate documents.jsonl
    docs = generate_documents(n_docs)
    with open(SEED_DIR / "documents.jsonl", "w") as f:
        for doc in docs:
            f.write(json.dumps(doc, ensure_ascii=False) + "\n")
    print(f"Generated {SEED_DIR / 'documents.jsonl'} ({len(docs)} rows)")


if __name__ == "__main__":
    main()
