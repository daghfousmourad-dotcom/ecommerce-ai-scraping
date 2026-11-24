"""
common_export.py
================
Module partagé garantissant la conformité stricte des 5 fichiers d'outputs
pour tous les scrapers unitaires du répertoire ./unit_scrapers/.
"""

from __future__ import annotations

import csv
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Dict, Union

REPAIR_KEYS = [
    "source_site", "source_url", "timestamp", "scrape_started_at", "data_type",
    "device_type", "device_family", "brand", "model", "model_normalized",
    "product_or_part_name", "repair_type_raw", "repair_type", "repair_type_matched",
    "price", "currency", "price_raw", "warranty", "delay",
    "shop_name", "city", "postal_code"
]

PRODUCT_SALE_KEYS = [
    "source_site", "source_url", "timestamp", "scrape_started_at", "data_type",
    "device_type", "device_family", "brand", "model", "model_normalized",
    "item_title", "storage_gb", "storage_raw", "ram_gb", "color", "condition",
    "product_id", "is_currently_sold", "price", "currency", "price_raw",
    "seller_name", "shipping_cost"
]


def now_iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def export_standard_artifacts(
    source_name: str,
    domain: str,
    rows: List[Dict[str, Any]],
    output_dir: str | Path,
    issues: List[Dict[str, Any]] | None = None,
    scrape_started_at: str | None = None,
    scrape_finished_at: str | None = None
) -> Dict[str, str]:
    """
    Exporte rigoureusement les 5 fichiers d'artefacts standardisés :
    1. db_<source_name>.json
    2. db_<source_name>.jsonl
    3. db_<source_name>.csv
    4. db_<source_name>_run.json
    5. db_<source_name>_issues.jsonl
    """
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    if domain == "repair_and_parts":
        keys = REPAIR_KEYS
    elif domain == "product_sales":
        keys = PRODUCT_SALE_KEYS
    else:
        raise ValueError(f"Domaine inconnu : {domain}. Utiliser 'repair_and_parts' ou 'product_sales'.")

    started_at = scrape_started_at or now_iso_utc()
    finished_at = scrape_finished_at or now_iso_utc()
    issues_list = issues or []

    # 1. Normalisation stricte des clés
    standardized_rows = []
    for r in rows:
        item = {}
        for k in keys:
            val = r.get(k)
            item[k] = val
        standardized_rows.append(item)

    prefix = f"db_{source_name}"
    json_path = out_path / f"{prefix}.json"
    jsonl_path = out_path / f"{prefix}.jsonl"
    csv_path = out_path / f"{prefix}.csv"
    run_path = out_path / f"{prefix}_run.json"
    issues_path = out_path / f"{prefix}_issues.jsonl"

    # Export JSON
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(standardized_rows, f, indent=2, ensure_ascii=False)

    # Export JSONL
    with open(jsonl_path, "w", encoding="utf-8") as f:
        for r in standardized_rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # Export CSV
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        writer.writerows(standardized_rows)

    # Export Issues
    with open(issues_path, "w", encoding="utf-8") as f:
        for iss in issues_list:
            f.write(json.dumps(iss, ensure_ascii=False) + "\n")

    # Export Run JSON
    run_metadata = {
        "source_site": source_name,
        "data_domain": domain,
        "scrape_started_at": started_at,
        "scrape_finished_at": finished_at,
        "status": "completed",
        "row_count": len(standardized_rows),
        "columns_count": len(keys),
        "columns": keys,
        "paths": {
            "json": str(json_path),
            "jsonl": str(jsonl_path),
            "csv": str(csv_path),
            "run_json": str(run_path),
            "issues_jsonl": str(issues_path)
        },
        "counts": {
            "fetch_errors": len(issues_list),
            "issues_total": len(issues_list)
        }
    }
    with open(run_path, "w", encoding="utf-8") as f:
        json.dump(run_metadata, f, indent=2)

    print(f"[{source_name.upper()}] Artifacts générés avec succès dans {out_path} ({len(standardized_rows)} lignes, {len(keys)} colonnes)")
    return run_metadata["paths"]
