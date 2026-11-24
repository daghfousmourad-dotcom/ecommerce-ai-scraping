"""JD Tech Service Scraper (https://jd-techservice.fr/tarifs-reparations).

Atelier de réparation informatique, smartphones et tablettes à Dunkerque et ses alentours :
- Tarifs par intervention et par modèle : Écran (Standard, Premium, Original), Batterie, Connecteur de charge, Vitre arrière.
- Couverture : Apple, Samsung, Google, Xiaomi, Honor, Oppo, OnePlus, Motorola, Nothing, Lenovo, Huawei.

Stratégie d'extraction (Niveau 1 — Appel Direct de l'API REST Interne /api/catalogue) :
    1. Pagination de l'endpoint JSON /api/catalogue?type=smartphone&page=...&limit=50
    2. Pagination de l'endpoint JSON /api/catalogue?type=tablet&page=...&limit=50
    3. Parsing des modèles et des grilles tarifaires multi-qualités (standardPrice, premiumPrice, originalPrice en centimes)
    4. Normalisation standard 22 colonnes E-commerce AI V2.
    5. Exportation certifiée des 5 artefacts standard (.jsonl, .csv, .json, _run.json, _issues.jsonl).
"""

from __future__ import annotations

import argparse
import json
import math
import ssl
import sys
import urllib.request
from pathlib import Path
from typing import Any

_SCRAPERS_ROOT = Path(__file__).resolve().parents[2]
if str(_SCRAPERS_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRAPERS_ROOT))
if str(_SCRAPERS_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(_SCRAPERS_ROOT.parent))
_CORE_DIR = _SCRAPERS_ROOT / "core"
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))

try:
    from scraper.core.filtering import CleaningConfig, clean_dataset
    from scraper.core.normalization import normalize_repair_type
    from scraper.core.parser_utils import dedupe_dict_rows, now_iso_utc
    from scraper.core.row_export import write_jsonl_and_csv
    from scraper.core.run_report import (
        REASON_FETCH_ERROR,
        fetch_issue,
        finalize_run_artifacts,
        summarize_run_line,
    )
except ImportError:
    from core.filtering import CleaningConfig, clean_dataset
    from core.normalization import normalize_repair_type
    from core.parser_utils import dedupe_dict_rows, now_iso_utc
    from core.row_export import write_jsonl_and_csv
    from core.run_report import (
        REASON_FETCH_ERROR,
        fetch_issue,
        finalize_run_artifacts,
        summarize_run_line,
    )

BASE_URL = "https://jd-techservice.fr"
SOURCE_NAME = "jd_techservice"
SOURCE_DOMAIN = "jd-techservice.fr"
API_URL = f"{BASE_URL}/api/catalogue"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)

SHOP_INFO = {
    "shop_name": "JD Tech Service (Dunkerque & Alentours)",
    "address": "Dunkerque et ses alentours (Atelier & Domicile)",
    "city": "Dunkerque",
    "postal_code": "59140",
    "delay": "Réparation express sur rendez-vous ou à domicile",
    "warranty": "Garantie pièces et main-d'œuvre",
}


def fetch_json(url: str, ctx: ssl.SSLContext, headers: dict[str, str]) -> tuple[dict[str, Any] | None, str | None]:
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=12) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data, None
    except Exception as e:
        return None, str(e)


def scrape(
    output_dir: str | Path | None = None,
    apply_cleaning: bool = True,
    emit_run_report: bool = True,
    max_models: int | None = None,
) -> list[dict[str, Any]]:
    scrape_started_at = now_iso_utc()
    out_dir = Path(output_dir) if output_dir else _SCRAPERS_ROOT / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    headers = {"User-Agent": USER_AGENT}

    print(f"[{SOURCE_NAME.upper()}] Étape 1 : Appel direct de l'API {API_URL}...", flush=True)

    rows: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []

    types_to_fetch = ["smartphone", "tablet"]

    for device_type in types_to_fetch:
        family = "smartphone" if device_type == "smartphone" else "tablet"
        page = 1
        limit = 50

        # Premier appel pour déterminer le total
        first_url = f"{API_URL}?type={device_type}&page={page}&limit={limit}"
        data, err = fetch_json(first_url, ctx, headers)

        if err:
            issues.append(fetch_issue(source_site=SOURCE_NAME, url=first_url, reason=REASON_FETCH_ERROR, message=err))
            print(f"[{SOURCE_NAME.upper()}] Erreur sur {first_url}: {err}", flush=True)
            continue

        if not data:
            continue

        total = data.get("total", 0)
        total_pages = math.ceil(total / limit)
        print(f"[{SOURCE_NAME.upper()}] Type '{device_type}' : {total} modèles identifiés ({total_pages} pages)", flush=True)

        all_models = list(data.get("models", []))

        # Pages suivantes
        for p in range(2, total_pages + 1):
            if max_models and len(all_models) >= max_models:
                break
            page_url = f"{API_URL}?type={device_type}&page={p}&limit={limit}"
            p_data, p_err = fetch_json(page_url, ctx, headers)
            if p_err:
                issues.append(fetch_issue(source_site=SOURCE_NAME, url=page_url, reason=REASON_FETCH_ERROR, message=p_err))
            elif p_data:
                all_models.extend(p_data.get("models", []))

        if max_models:
            all_models = all_models[:max_models]

        # Parsing des modèles
        for m in all_models:
            brand = m.get("brand") or "Générique"
            model_name = m.get("model") or "Modèle inconnu"
            model_slug = m.get("slug") or model_name.lower().replace(" ", "-")
            source_page_url = f"{BASE_URL}/tarifs-reparations/{'smartphones' if family == 'smartphone' else 'tablettes'}"

            prices = m.get("prices", [])
            for p_obj in prices:
                base_label = p_obj.get("label", "").strip()
                if not base_label:
                    continue

                qualities = [
                    ("standard", p_obj.get("standardPrice")),
                    ("premium", p_obj.get("premiumPrice")),
                    ("original", p_obj.get("originalPrice")),
                ]

                for q_name, q_cents in qualities:
                    if q_cents is None:
                        continue
                    try:
                        price = float(q_cents) / 100.0
                    except (ValueError, TypeError):
                        continue

                    if price <= 0:
                        continue

                    raw_label = f"{base_label} ({q_name.capitalize()})" if q_name != "standard" else base_label
                    norm_repair = normalize_repair_type(base_label)

                    row = {
                        "source_site": SOURCE_DOMAIN,
                        "source_url": source_page_url,
                        "source": source_page_url,
                        "timestamp": scrape_started_at,
                        "data_type": "repair_price",
                        "device_family": family,
                        "brand": brand,
                        "model": model_name,
                        "item_name": f"{raw_label} {brand} {model_name}",
                        "repair_type_raw": raw_label,
                        "repair_type": norm_repair,
                        "price": price,
                        "price_raw": f"{int(price) if price.is_integer() else price} €",
                        "currency": "EUR",
                        "currency_symbol": "€",
                        "shop_name": SHOP_INFO["shop_name"],
                        "address": SHOP_INFO["address"],
                        "city": SHOP_INFO["city"],
                        "postal_code": SHOP_INFO["postal_code"],
                        "delay": SHOP_INFO["delay"],
                        "warranty": SHOP_INFO["warranty"],
                        "model_sku": model_name.lower().replace(" ", "_"),
                        "repair_category": "Réparation en atelier / à domicile",
                        "scraped_at": scrape_started_at,
                    }
                    rows.append(row)

    initial_count = len(rows)
    rows = dedupe_dict_rows(rows, keys=("source_site", "brand", "model", "repair_type_raw", "price"))
    print(f"[{SOURCE_NAME.upper()}] Total lignes brutes: {initial_count}, après déduplication: {len(rows)}", flush=True)

    clean_report = None
    fetch_errors = len(issues)
    if apply_cleaning:
        cleaning_cfg = CleaningConfig(
            price_min=10.0,
            price_max=4000.0,
            dedupe_keys_repair_price=("source_url", "model", "repair_type_raw", "price_raw"),
        )
        rows, clean_report = clean_dataset(
            rows,
            data_type="repair_price",
            config=cleaning_cfg,
            cleaning_issues=issues if emit_run_report else None,
        )
        print(f"[{SOURCE_NAME.upper()}] Après nettoyage qualité: {len(rows)} tarifs valides", flush=True)

    # Export des 5 artefacts standard
    jsonl_path = out_dir / f"{SOURCE_NAME}.jsonl"
    csv_path = out_dir / f"{SOURCE_NAME}.csv"
    json_path = out_dir / f"{SOURCE_NAME}.json"

    write_jsonl_and_csv(rows, jsonl_path=jsonl_path, csv_path=csv_path)

    payload = {
        "source": SOURCE_NAME,
        "scraped_at": scrape_started_at,
        "count": len(rows),
        "rows": rows,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n[{SOURCE_NAME.upper()}] ✅ Succès : {len(rows)} tarifs exportés dans {out_dir}", flush=True)

    if emit_run_report:
        payload_report = finalize_run_artifacts(
            source_site=SOURCE_NAME,
            scrape_started_at=scrape_started_at,
            json_path=json_path,
            jsonl_path=jsonl_path,
            csv_path=csv_path,
            row_count=len(rows),
            apply_cleaning=apply_cleaning,
            clean_report=clean_report,
            fetch_errors=fetch_errors,
            issues=issues,
        )
        print(summarize_run_line(payload_report), flush=True)

    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=f"{SOURCE_NAME} Scraper")
    parser.add_argument("--output-dir", default=str(_SCRAPERS_ROOT / "outputs"), help="Dossier de sortie")
    parser.add_argument("--no-clean", action="store_true", help="Désactiver le nettoyage")
    parser.add_argument("--no-report", action="store_true", help="Désactiver le rapport")
    parser.add_argument("--max-models", type=int, default=None, help="Max modèles")
    args = parser.parse_args()

    scrape(
        output_dir=args.output_dir,
        apply_cleaning=not args.no_clean,
        emit_run_report=not args.no_report,
        max_models=args.max_models,
    )


if __name__ == "__main__":
    main()
