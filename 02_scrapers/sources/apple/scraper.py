"""
Apple iPhone Repair Official Scraper (sources2).

Extrait les tarifs officiels de réparation constructeur Apple France :
  API: https://support.apple.com/ols/api/pricing/products/services/pricing-estimate
  Page: https://support.apple.com/fr-fr/iphone/repair
  Type de tarification: OOW (Out Of Warranty / Hors Garantie France)
"""

from __future__ import annotations

import sys
from pathlib import Path
_SCRAPERS_ROOT = Path(__file__).resolve().parents[2]
if str(_SCRAPERS_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRAPERS_ROOT))
_CORE_DIR = _SCRAPERS_ROOT / 'core'
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))

import sys
from pathlib import Path
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import csv
import json
import re
from pathlib import Path
from typing import Any

from tqdm import tqdm

from scraper.core.filtering import CleaningConfig, clean_dataset
from scraper.core.http_client import create_session
from scraper.core.normalization import normalize_device_name, normalize_repair_type
from scraper.core.parser_utils import (
    dedupe_dict_rows,
    now_iso_utc,
    parse_price_and_currency,
)
from scraper.core.row_export import write_jsonl_and_csv
from scraper.core.run_report import (
    REASON_FETCH_ERROR,
    fetch_issue,
    finalize_run_artifacts,
    summarize_run_line,
)

APPLE_REPAIR_URL = "https://support.apple.com/fr-fr/iphone/repair"
APPLE_ESTIMATE_API_URL = (
    "https://support.apple.com/ols/api/pricing/products/services/pricing-estimate"
    "?locale=fr-fr&pricing_type=OOW&parent_tag_id=TAG_1754518739895"
)


def fetch_apple_repair_catalog(session=None) -> list[dict[str, Any]]:
    """
    Interroge l'API officielle Apple d'estimation des tarifs de réparation hors garantie (France).
    """
    if session is None:
        session = create_session()

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
        "Referer": APPLE_REPAIR_URL,
        "Accept": "application/json",
    }
    resp = session.get(APPLE_ESTIMATE_API_URL, headers=headers, timeout=25)
    resp.raise_for_status()
    payload = resp.json()

    products = payload.get("products", [])
    models: list[dict[str, Any]] = []

    for p in products:
        children = p.get("childrenProducts", [])
        if children:
            for child in children:
                title = child.get("product_loc_title") or child.get("product_eng_title") or ""
                services = child.get("services", [])
                if title and services:
                    models.append({"model_name": title, "services": services})
        else:
            title = p.get("product_loc_title") or p.get("product_eng_title") or ""
            services = p.get("services", [])
            if title and services:
                models.append({"model_name": title, "services": services})

    return models


def clean_apple_price_string(price_str: str) -> str:
    """
    Normalise le séparateur des milliers européen (ex: €1.059 -> €1059).
    """
    if not price_str:
        return ""
    # Remplace le point séparateur de milliers par une chaîne vide
    return re.sub(r"(\d+)\.(\d{3})\b", r"\1\2", price_str.strip())


def scrape(
    *,
    output_dir: str = str(_SCRAPERS_ROOT / "outputs"),
    apply_cleaning: bool = True,
    emit_run_report: bool = True,
) -> list[dict[str, Any]]:
    """
    Exécute le scraping complet des tarifs de réparation Apple iPhone officiels.
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    scrape_started_at = now_iso_utc()
    ts_suffix = (
        scrape_started_at.replace(":", "")
        .replace("-", "")
        .replace("+", "")
        .replace("T", "_")
    )

    session = create_session()
    models_data = fetch_apple_repair_catalog(session=session)
    if not models_data:
        raise RuntimeError("Aucun modèle ou tarif retourné par l'API Apple.")

    all_rows: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    fetch_errors = 0

    jsonl_path = out_dir / f"apple_repair_prices_{ts_suffix}.jsonl"
    json_path = out_dir / f"apple_repair_prices_{ts_suffix}.json"
    csv_path = out_dir / f"apple_repair_prices_{ts_suffix}.csv"

    jsonl_file = jsonl_path.open("w", encoding="utf-8")
    csv_file = csv_path.open("w", encoding="utf-8", newline="")
    csv_writer: csv.DictWriter | None = None

    def _stream_row(row: dict[str, Any]) -> None:
        nonlocal csv_writer
        row_with_meta = dict(row)
        row_with_meta.setdefault("scrape_started_at", scrape_started_at)
        all_rows.append(row_with_meta)

        jsonl_file.write(json.dumps(row_with_meta, ensure_ascii=False) + "\n")
        jsonl_file.flush()

        if csv_writer is None:
            fieldnames = list(row_with_meta.keys())
            csv_writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
            csv_writer.writeheader()
        csv_writer.writerow(row_with_meta)
        csv_file.flush()

    timestamp = now_iso_utc()

    with tqdm(
        total=len(models_data),
        desc="[Apple Repair] Parsing",
        unit="modèle",
        dynamic_ncols=True,
    ) as pbar:
        for item in models_data:
            raw_model = item.get("model_name", "").strip()
            services = item.get("services", [])

            dev = normalize_device_name("smartphone", brand="Apple", model=raw_model)
            model_str = dev.get("model") or raw_model
            model_norm = (model_str or "").lower().strip()

            model_slug = re.sub(r"[^a-z0-9]+", "-", model_norm).strip("-")
            model_url = f"{APPLE_REPAIR_URL}#{model_slug}"

            for srv in services:
                raw_label = srv.get("serviceLabel", "").strip()
                raw_price = srv.get("price", "").strip()
                if not raw_label or not raw_price:
                    continue

                cleaned_price_str = clean_apple_price_string(raw_price)
                price_val, currency = parse_price_and_currency(cleaned_price_str)
                if price_val is None:
                    continue

                repair_norm, repair_matched = normalize_repair_type(raw_label)

                row = {
                    "source_site": "apple",
                    "source_url": model_url,
                    "timestamp": timestamp,
                    "scrape_started_at": scrape_started_at,
                    "data_type": "repair_price",
                    "device_type": "smartphone",
                    "device_family": dev.get("device_family"),
                    "brand": "Apple",
                    "model": model_str,
                    "model_normalized": model_norm,
                    "product_or_part_name": f"{raw_label} - {model_str}",
                    "repair_type_raw": raw_label,
                    "repair_type": repair_norm,
                    "repair_type_matched": repair_matched,
                    "price": price_val,
                    "currency": currency or "EUR",
                    "price_raw": raw_price,
                    "warranty": "Pièces d'origine Apple garanties 90 jours",
                    "delay": "Sur place ou envoi",
                    "shop_name": "Apple Store / Apple Support France",
                    "city": "France",
                    "postal_code": "",
                }
                _stream_row(row)

            pbar.update(1)

    jsonl_file.close()
    csv_file.close()

    all_rows = dedupe_dict_rows(all_rows, ("source_url", "model_normalized", "repair_type_raw", "price_raw"))
    clean_report = None
    if apply_cleaning:
        cfg = CleaningConfig(dedupe_keys_repair_price=("source_url", "repair_type_raw", "price_raw"))
        all_rows, clean_report = clean_dataset(
            all_rows,
            data_type="repair_price",
            config=cfg,
            cleaning_issues=issues if emit_run_report else None,
        )
        print(clean_report.summary_line(), flush=True)

    for row in all_rows:
        row.setdefault("scrape_started_at", scrape_started_at)

    write_jsonl_and_csv(all_rows, jsonl_path=jsonl_path, csv_path=csv_path)

    payload = {
        "source_site": "apple",
        "scrape_started_at": scrape_started_at,
        "row_count": len(all_rows),
        "rows": all_rows,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[Apple Repair] Terminé. Exporté {len(all_rows)} lignes", flush=True)
    if emit_run_report:
        payload_report = finalize_run_artifacts(
            source_site="apple",
            scrape_started_at=scrape_started_at,
            json_path=json_path,
            jsonl_path=jsonl_path,
            csv_path=csv_path,
            row_count=len(all_rows),
            apply_cleaning=apply_cleaning,
            clean_report=clean_report,
            fetch_errors=fetch_errors,
            issues=issues,
        )
        print(summarize_run_line(payload_report), flush=True)

    return all_rows


if __name__ == "__main__":
    scrape()
