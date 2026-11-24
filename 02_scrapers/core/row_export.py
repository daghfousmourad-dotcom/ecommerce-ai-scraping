"""Write row lists to JSONL + CSV (used after dedupe / cleaning for consistent artifacts)."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


def write_jsonl_and_csv(
    rows: list[dict[str, Any]],
    *,
    jsonl_path: Path,
    csv_path: Path,
) -> None:
    """Overwrite JSONL and CSV with the same row list (field order from first row)."""
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        jsonl_path.write_text("", encoding="utf-8")
        csv_path.write_text("", encoding="utf-8")
        return
    jsonl_path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8",
    )
    with csv_path.open("w", encoding="utf-8", newline="") as cf:
        writer = csv.DictWriter(cf, fieldnames=list(rows[0].keys()), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
