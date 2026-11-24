"""
Samsung Smart Repair Scraper (sources2).

Extrait les tarifs officiels de réparation constructeur Samsung France :
  URL: https://www.samsung.com/fr/support/smart-repair-service/mobile-devices/
  Couvre les smartphones Galaxy (Z Fold, Z Flip, S Series, A Series, Note),
  les tablettes Galaxy Tab, les montres Galaxy Watch et Galaxy Ring.
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

import csv
import json
import re
from pathlib import Path
from typing import Any

from tqdm import tqdm

from scraper.core.filtering import clean_dataset
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

SMART_REPAIR_URL = "https://www.samsung.com/fr/support/smart-repair-service/mobile-devices/"

REPAIR_LABEL_MAPPING = {
    "Ecran Principal": "Écran",
    "Ecran Externe": "Écran externe",
    "Batterie": "Batterie",
    "Connecteur de charge": "Connecteur de charge",
    "Vitre arrière": "Vitre arrière",
    "Caméra": "Caméra arrière",
    "Anneau": "Anneau",
    "Boitier de Charge": "Boîtier de charge",
}


def fetch_smart_repair_data(session=None) -> tuple[list[str], list[dict[str, Any]]]:
    """
    Télécharge et extrait les données brutes JSON intégrées dans la page Samsung Smart Repair.
    """
    if session is None:
        session = create_session()

    resp = session.get(SMART_REPAIR_URL, timeout=25)
    resp.raise_for_status()
    html_text = resp.text

    # Extraction des types d'interventions (headers)
    m_headers = re.search(r"var\s+headers\s*=\s*(\[[^\]]+\]);", html_text)
    if not m_headers:
        raise RuntimeError("Impossible de trouver 'var headers' dans la page Samsung Smart Repair.")
    headers = json.loads(m_headers.group(1))

    # Extraction des modèles et tarifs (dataAll)
    m_data = re.search(r"var\s+dataAll\s*=\s*(\[\{.*?\}\]);", html_text, re.DOTALL)
    if not m_data:
        raise RuntimeError("Impossible de trouver 'var dataAll' dans la page Samsung Smart Repair.")
    data_all = json.loads(m_data.group(1))

    return headers, data_all


def scrape(
    *,
    output_dir: str = str(_SCRAPERS_ROOT / "outputs"),
    apply_cleaning: bool = True,
    emit_run_report: bool = True,
) -> list[dict[str, Any]]:
    """
    Exécute le scraping complet de Samsung Smart Repair.
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
    headers_list, models_data = fetch_smart_repair_data(session=session)

    all_rows: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    fetch_errors = 0

    jsonl_path = out_dir / f"samsung_repair_prices_{ts_suffix}.jsonl"
    json_path = out_dir / f"samsung_repair_prices_{ts_suffix}.json"
    csv_path = out_dir / f"samsung_repair_prices_{ts_suffix}.csv"

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
        desc="[Samsung Smart Repair] Parsing",
        unit="modèle",
        dynamic_ncols=True,
    ) as pbar:
        for idx, item in enumerate(models_data, start=1):
            raw_model = item.get("model", "").strip()
            prices = item.get("prices", [])
            note = item.get("note", "")

            # Détermination du type d'appareil
            device_type = "smartphone"
            m_upper = raw_model.upper()
            if "TAB" in m_upper:
                device_type = "tablette"
            elif "WATCH" in m_upper:
                device_type = "montre"
            elif "RING" in m_upper:
                device_type = "objet-connecte"
            elif "BOOK" in m_upper:
                device_type = "ordinateur-portable"

            dev = normalize_device_name(device_type, brand="Samsung", model=raw_model)
            model_str = dev.get("model") or raw_model
            model_norm = (model_str or "").lower().strip()

            model_slug = re.sub(r"[^a-z0-9]+", "-", model_norm).strip("-")
            model_url = f"{SMART_REPAIR_URL}#{model_slug}"

            for h_idx, raw_price in enumerate(prices):
                if not raw_price:
                    continue

                raw_header = headers_list[h_idx] if h_idx < len(headers_list) else f"Intervention {h_idx}"
                normalized_input = REPAIR_LABEL_MAPPING.get(raw_header, raw_header)
                price_val, currency = parse_price_and_currency(str(raw_price))

                if price_val is None:
                    continue

                repair_norm, repair_matched = normalize_repair_type(normalized_input)

                row = {
                    "source_site": "samsung",
                    "source_url": model_url,
                    "timestamp": timestamp,
                    "scrape_started_at": scrape_started_at,
                    "data_type": "repair_price",
                    "device_type": device_type,
                    "device_family": dev.get("device_family"),
                    "brand": "Samsung",
                    "model": model_str,
                    "model_normalized": model_norm,
                    "product_or_part_name": f"{raw_header} - {model_str}",
                    "repair_type_raw": raw_header,
                    "repair_type": repair_norm,
                    "repair_type_matched": repair_matched,
                    "price": price_val,
                    "currency": currency or "EUR",
                    "price_raw": str(raw_price),
                    "warranty": "Pièces d'origine garanties 1 an",
                    "delay": "Réparation express",
                    "shop_name": "Samsung Smart Repair Officiel",
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
        from scraper.core.filtering import CleaningConfig
        cfg = CleaningConfig(dedupe_keys_repair_price=("source_url", "model_normalized", "repair_type_raw", "price_raw"))
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
        "source_site": "samsung",
        "scrape_started_at": scrape_started_at,
        "row_count": len(all_rows),
        "rows": all_rows,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[Samsung Smart Repair] Terminé. Exporté {len(all_rows)} lignes", flush=True)
    if emit_run_report:
        payload_report = finalize_run_artifacts(
            source_site="samsung",
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
