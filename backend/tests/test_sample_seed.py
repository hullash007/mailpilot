"""The no-credential demo must render classified synthetic records."""
import os
from pathlib import Path
import sqlite3
import subprocess
import sys


def test_seed_demo_classifies_only_synthetic_records(tmp_path):
    root = Path(__file__).resolve().parents[2]
    db_path = tmp_path / "demo.db"
    env = os.environ.copy()
    env.update(DATABASE_URL="sqlite:///" + str(db_path), LLM_REFINE="0")
    subprocess.run([sys.executable, "scripts/seed_sample_data.py"],
                   cwd=root, env=env, check=True, capture_output=True)
    with sqlite3.connect(db_path) as db:
        rows = db.execute("SELECT is_sample, category, priority, classification_source, classified_at FROM emails").fetchall()
    assert len(rows) == 6
    assert all(sample == 1 and category and priority and source == "rules" and at
               for sample, category, priority, source, at in rows)
    assert ("notification", "high") in [(r[1], r[2]) for r in rows]
