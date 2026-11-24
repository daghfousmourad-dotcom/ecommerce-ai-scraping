"""iRiparo scraper: discover devices and repair prices via official JSON API.

Flux de découverte et scraping :
  1. GET https://parislecourbe.iriparo.com/iriparo_dev/get_brands.php (Liste des 48 marques)
  2. GET https://parislecourbe.iriparo.com/iriparo_dev/get_devices.php?brand=<brand> (Modèles d'appareils)
  3. GET https://parislecourbe.iriparo.com/iriparo_dev/get_interventions.php?organization_id=2&device_id=<id> (Tarifs officiels en France)
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
from pathlib import Path
from typing import Any

from tqdm import tqdm

from scraper.core.filtering import clean_dataset
from scraper.core.http_client import create_session, polite_sleep
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

BASE_API_URL = "https://parislecourbe.iriparo.com/iriparo_dev"
ORGANIZATION_ID_FRANCE = 2


def discover_devices(session=None, limit_brands: list[str] | None = None) -> list[dict[str, Any]]:
    """
    Découvre l'ensemble des marques et appareils disponibles dans l'API iRiparo.
    """
    print("[iRiparo] Découverte des marques via l'API...", flush=True)
    if session is None:
        session = create_session()

    brands_url = f"{BASE_API_URL}/get_brands.php"
    resp = session.get(brands_url, timeout=20)
    resp.raise_for_status()
    all_brands = resp.json()

    if limit_brands:
        brands = [b for b in all_brands if b.strip().lower() in [lb.lower() for lb in limit_brands]]
    else:
        brands = all_brands

    print(f"[iRiparo] {len(brands)} marque(s) sélectionnée(s). Récupération des modèles...", flush=True)

    discovered_devices: list[dict[str, Any]] = []

    for brand in brands:
        b_clean = brand.strip()
        if not b_clean:
            continue
        try:
            r_dev = session.get(
                f"{BASE_API_URL}/get_devices.php",
                params={"brand": b_clean},
                timeout=20,
            )
            if r_dev.status_code != 200:
                continue
            dev_groups = r_dev.json()
            for group in dev_groups:
                category = group.get("text", "")
                for child in group.get("children", []):
                    device_id = str(child.get("id", "")).strip()
                    model_name = child.get("text", "").strip()
                    if device_id and model_name:
                        discovered_devices.append(
                            {
                                "brand": b_clean,
                                "device_id": device_id,
                                "model": model_name,
                                "category": category,
                            }
                        )
        except Exception as exc:
            print(f"[iRiparo] Erreur découverte modèles pour {b_clean}: {exc}", flush=True)
        polite_sleep(0.1, 0.25)

    print(f"[iRiparo] Découverte terminée : {len(discovered_devices)} appareil(s) identifié(s)", flush=True)
    return discovered_devices


def _parse_interventions_for_device(
    session,
    device_info: dict[str, Any],
    *,
    organization_id: int = ORGANIZATION_ID_FRANCE,
    scrape_started_at: str | None = None,
) -> list[dict[str, Any]]:
    """
    Interroge l'API pour récupérer toutes les interventions tarifées d'un appareil donné.
    """
    brand_raw = device_info["brand"]
    model_raw = device_info["model"]
    device_id = device_info["device_id"]
    category_raw = device_info.get("category", "").lower()

    # Détermination du type d'appareil
    device_type = "smartphone"
    if "tablet" in category_raw or "ipad" in category_raw or "tab" in category_raw:
        device_type = "tablette"
    elif "watch" in category_raw or "montre" in category_raw:
        device_type = "montre"
    elif "pc" in category_raw or "ordinateur" in category_raw or "mac" in category_raw:
        device_type = "ordinateur-portable"

    dev = normalize_device_name(device_type, brand=brand_raw, model=model_raw)
    model_str = dev.get("model") or model_raw
    model_norm = (model_str or "").lower().strip()

    params = {
        "organization_id": organization_id,
        "device_id": device_id,
    }
    r = session.get(f"{BASE_API_URL}/get_interventions.php", params=params, timeout=20)
    if r.status_code != 200:
        return []

    interventions = r.json()
    if not isinstance(interventions, list):
        return []

    timestamp = now_iso_utc()
    records: list[dict[str, Any]] = []

    for item in interventions:
        repair_name = item.get("name", "").strip()
        if not repair_name:
            continue

        raw_price_cents = item.get("price_cents")
        currency = item.get("currency") or "EUR"

        if raw_price_cents is None:
            continue

        try:
            price = float(raw_price_cents)
        except (ValueError, TypeError):
            continue

        raw_price_str = f"{price:.2f} {currency}"
        repair_norm, repair_matched = normalize_repair_type(repair_name)

        # Warranty format
        warranty_period = item.get("warranty_period")
        warranty = f"{warranty_period} mois" if warranty_period else "12 mois"

        records.append(
            {
                "source_site": "iriparo",
                "source_url": f"https://iriparo.com/fr/assistance-reparations/?device_id={device_id}",
                "timestamp": timestamp,
                "scrape_started_at": scrape_started_at,
                "data_type": "repair_price",
                "device_type": device_type,
                "device_family": dev.get("device_family"),
                "brand": dev.get("brand") or brand_raw,
                "model": model_str,
                "model_normalized": model_norm,
                "product_or_part_name": repair_name,
                "repair_type_raw": repair_name,
                "repair_type": repair_norm,
                "repair_type_matched": repair_matched,
                "price": price,
                "currency": currency,
                "price_raw": raw_price_str,
                "warranty": warranty,
                "delay": None,
                "shop_name": "iRiparo (Paris Lecourbe - Ref France)",
                "city": "Paris",
                "postal_code": "75015",
            }
        )

    return dedupe_dict_rows(records, ("source_url", "repair_type_raw", "price_raw"))


def scrape(
    *,
    output_dir: str = str(_SCRAPERS_ROOT / "outputs"),
    limit_brands: list[str] | None = None,
    devices: list[dict[str, Any]] | None = None,
    apply_cleaning: bool = True,
    emit_run_report: bool = True,
) -> list[dict[str, Any]]:
    """
    Exécute le scraping complet d'iRiparo via API directe.
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
    all_devices = devices or discover_devices(session=session, limit_brands=limit_brands)
    if not all_devices:
        raise RuntimeError("Aucun appareil iRiparo découvert.")

    all_rows: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    fetch_errors = 0

    jsonl_path = out_dir / f"iriparo_repair_prices_{ts_suffix}.jsonl"
    json_path = out_dir / f"iriparo_repair_prices_{ts_suffix}.json"
    csv_path = out_dir / f"iriparo_repair_prices_{ts_suffix}.csv"

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

    with tqdm(
        total=len(all_devices),
        desc="[iRiparo] Scraping API",
        unit="appareil",
        dynamic_ncols=True,
    ) as pbar:
        for index, dev_info in enumerate(all_devices, start=1):
            try:
                rows = _parse_interventions_for_device(
                    session, dev_info, scrape_started_at=scrape_started_at
                )
                for r in rows:
                    _stream_row(r)
                pbar.set_postfix(rows=len(rows), refresh=False)
            except Exception as exc:
                fetch_errors += 1
                if emit_run_report:
                    issues.append(
                        fetch_issue(
                            reason=REASON_FETCH_ERROR,
                            source_site="iriparo",
                            source_url=f"https://iriparo.com/fr/assistance-reparations/?device_id={dev_info.get('device_id')}",
                            page_index=index,
                            detail=str(exc),
                        )
                    )
            pbar.update(1)
            polite_sleep(0.05, 0.15)

    jsonl_file.close()
    csv_file.close()

    all_rows = dedupe_dict_rows(all_rows, ("source_url", "repair_type_raw", "price_raw"))
    clean_report = None
    if apply_cleaning:
        all_rows, clean_report = clean_dataset(
            all_rows,
            data_type="repair_price",
            cleaning_issues=issues if emit_run_report else None,
        )
        print(clean_report.summary_line(), flush=True)

    for row in all_rows:
        row.setdefault("scrape_started_at", scrape_started_at)

    write_jsonl_and_csv(all_rows, jsonl_path=jsonl_path, csv_path=csv_path)

    payload = {
        "source_site": "iriparo",
        "scrape_started_at": scrape_started_at,
        "row_count": len(all_rows),
        "rows": all_rows,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[iRiparo] Terminé. Exporté {len(all_rows)} lignes", flush=True)
    if emit_run_report:
        payload_report = finalize_run_artifacts(
            source_site="iriparo",
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
