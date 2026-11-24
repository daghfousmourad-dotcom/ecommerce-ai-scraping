"""OPPO France Scraper (https://support.oppo.com/fr/spare-parts-price/).

Tarifs officiels des pièces détachées et réparations constructeur OPPO France :
Stratégie ordonnée (Roadmap E-commerce AI Scraping) :
  1. API Directe (POST JSON REST) :
     - Liste des modèles officiels : POST https://par-sow-cms.oppo.com/oppo-api/basic/v1/getProduct
     - Tarifs indicatifs des pièces par modèle : POST https://par-sow-cms.oppo.com/oppo-api/basic/v1/getPartPriceNew
     Fournit 100% des tarifs officiels constructeur OPPO en France en quelques secondes.
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

import argparse
import csv
import json
import re
from pathlib import Path
from typing import Any

from tqdm import tqdm

from scraper.core.filtering import CleaningConfig, clean_dataset
from scraper.core.http_client import create_session
from scraper.core.normalization import normalize_repair_type
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

BASE_PAGE_URL = "https://support.oppo.com/fr/spare-parts-price/"
API_GET_PRODUCT = "https://par-sow-cms.oppo.com/oppo-api/basic/v1/getProduct"
API_GET_PRICE = "https://par-sow-cms.oppo.com/oppo-api/basic/v1/getPartPriceNew"
SOURCE_NAME = "oppo"
SOURCE_DOMAIN = "support.oppo.com"

DEFAULT_HEADERS = {
    "Referer": "https://support.oppo.com/",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Content-Type": "application/json; charset=UTF-8",
    "Accept": "application/json, text/plain, */*",
}

SHOP_INFO = {
    "shop_name": "OPPO France Service Officiel",
    "address": "Support en ligne et centres agréés",
    "city": "National",
    "postal_code": "75000",
    "delay": "Centre de service officiel OPPO",
    "warranty": "Garantie constructeur pièces d'origine",
}


def fetch_all_products(session=None) -> list[dict[str, Any]]:
    """Récupère l'ensemble des produits référencés sur le portail de support OPPO France."""
    if session is None:
        session = create_session()

    payload = {
        "region": "fr",
        "regionIsoCode2": "fr",
        "brandCode": "11",
        "isoLanguageCode": "fr-FR",
        "sourceRoute": "1",
    }
    resp = session.post(API_GET_PRODUCT, headers=DEFAULT_HEADERS, json=payload, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != "1" and data.get("code") != 1:
        raise ValueError(f"Erreur API getProduct: code {data.get('code')} - {data.get('msg')}")

    return data.get("data") or []


def fetch_model_prices(marketing_model_code: str, session=None) -> dict[str, Any]:
    """Récupère la grille tarifaire complète des pièces détachées d'un modèle."""
    if session is None:
        session = create_session()

    payload = {
        "region": "fr",
        "marketingModelCode": marketing_model_code,
        "regionIsoCode2": "fr",
        "isoLanguageCode": "fr-FR",
        "sourceRoute": "1",
    }
    resp = session.post(API_GET_PRICE, headers=DEFAULT_HEADERS, json=payload, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    return data.get("data") or {}


def scrape(
    *,
    output_dir: str = str(_SCRAPERS_ROOT / "outputs"),
    apply_cleaning: bool = True,
    emit_run_report: bool = True,
    max_models: int | None = None,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    """Point d'entrée principal du scraper OPPO France."""
    scrape_started_at = now_iso_utc()
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    timestamp = re.sub(r"[-:T]", "", scrape_started_at.split(".")[0]) + "0000"
    stem = f"oppo_repair_prices_{timestamp}"

    json_path = out_dir / f"{stem}.json"
    jsonl_path = out_dir / f"{stem}.jsonl"
    csv_path = out_dir / f"{stem}.csv"

    session = create_session()
    all_rows: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    fetch_errors = 0

    print(f"[{SOURCE_NAME}] Découverte des modèles officiels via l'API OPPO France...", flush=True)
    try:
        products = fetch_all_products(session)
    except Exception as e:
        print(f"[{SOURCE_NAME}] Erreur lors de la récupération des produits: {e}", flush=True)
        fetch_errors += 1
        issues.append(
            fetch_issue(
                reason=REASON_FETCH_ERROR,
                source_site=SOURCE_NAME,
                source_url=API_GET_PRODUCT,
                detail=str(e),
            )
        )
        return []

    print(f"[{SOURCE_NAME}] {len(products)} modèles identifiés.", flush=True)
    if max_models and max_models > 0:
        products = products[:max_models]
        print(f"[{SOURCE_NAME}] Limite active : {max_models} modèles.")

    scraped_at = now_iso_utc()

    for p in tqdm(products, desc="OPPO Models", unit="modèle"):
        model_code = p.get("marketingModelCode")
        model_name = p.get("marketingModelName") or "Modèle OPPO"
        cat_name = p.get("categoryName") or "Smartphone"
        device_family = "Tablette" if "tablette" in cat_name.lower() or "pad" in model_name.lower() else "Smartphone"

        if not model_code:
            continue

        model_url = f"{BASE_PAGE_URL}#/detail?marketingModelCode={model_code}"

        try:
            price_data = fetch_model_prices(model_code, session=session)
            part_list = price_data.get("partPriceList") or []
        except Exception as e:
            fetch_errors += 1
            issues.append(
                fetch_issue(
                    reason=REASON_FETCH_ERROR,
                    source_site=SOURCE_NAME,
                    source_url=model_url,
                    detail=str(e),
                )
            )
            continue

        for cat_item in part_list:
            cat_label = cat_item.get("lv3ClassificationName") or cat_item.get("lv2ClassificationName") or ""
            children = cat_item.get("childList") or []

            # Si pas d'enfants, utiliser l'élément parent s'il a un prix
            if not children and cat_item.get("retailPrice") is not None:
                children = [cat_item]

            for child in children:
                part_name = child.get("partName") or cat_label or "Pièce détachée"
                raw_price = child.get("retailPrice")
                currency = child.get("retailPriceCurrency") or "EUR"

                if raw_price is None:
                    continue

                try:
                    price_val = float(raw_price)
                except (ValueError, TypeError):
                    continue

                if price_val <= 0:
                    continue

                # Normalisation de la panne
                canon_repair, is_matched = normalize_repair_type(part_name)
                if not is_matched and cat_label:
                    canon_from_cat, cat_matched = normalize_repair_type(cat_label)
                    if cat_matched:
                        canon_repair = canon_from_cat
                        is_matched = True

                repair_type = canon_repair if canon_repair else part_name

                all_rows.append(
                    {
                        "source_site": SOURCE_DOMAIN,
                        "source_url": model_url,
                        "source": model_url,
                        "timestamp": scraped_at,
                        "data_type": "repair_price",
                        "device_family": device_family,
                        "brand": "OPPO",
                        "model": model_name.strip(),
                        "item_name": part_name.strip(),
                        "repair_type_raw": part_name.strip(),
                        "repair_type": repair_type,
                        "repair_type_matched": bool(is_matched),
                        "price": price_val,
                        "currency": currency,
                        "price_raw": f"{price_val:.2f} {currency}",
                        "delay": SHOP_INFO["delay"],
                        "warranty": SHOP_INFO["warranty"],
                        "shop_name": SHOP_INFO["shop_name"],
                        "address": SHOP_INFO["address"],
                        "city": SHOP_INFO["city"],
                        "postal_code": SHOP_INFO["postal_code"],
                        "scrape_started_at": scrape_started_at,
                    }
                )

    print(f"\n[{SOURCE_NAME}] Extraction brute terminée : {len(all_rows)} tarifs avec prix.", flush=True)

    # Déduplication
    all_rows = dedupe_dict_rows(all_rows, ("brand", "model", "repair_type_raw", "price_raw"))

    # Nettoyage standard du pipeline
    clean_report = None
    if apply_cleaning:
        cleaning_cfg = CleaningConfig(
            dedupe_keys_repair_price=("source_url", "repair_type_raw", "price_raw")
        )
        all_rows, clean_report = clean_dataset(
            all_rows,
            data_type="repair_price",
            config=cleaning_cfg,
            cleaning_issues=issues if emit_run_report else None,
        )
        if clean_report:
            print(clean_report.summary_line(), flush=True)

    for row in all_rows:
        row.setdefault("scrape_started_at", scrape_started_at)

    # Écriture des 5 fichiers de sortie standard
    write_jsonl_and_csv(all_rows, jsonl_path=jsonl_path, csv_path=csv_path)

    payload = {
        "source_site": SOURCE_NAME,
        "scrape_started_at": scrape_started_at,
        "row_count": len(all_rows),
        "rows": all_rows,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n[OPPO] Terminé avec succès. {len(all_rows)} tarifs exportés dans {out_dir}", flush=True)

    if emit_run_report:
        payload_report = finalize_run_artifacts(
            source_site=SOURCE_NAME,
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


def main() -> None:
    parser = argparse.ArgumentParser(description="OPPO France Spare Parts Scraper")
    parser.add_argument("--output-dir", default=str(_SCRAPERS_ROOT / "outputs"), help="Dossier de sortie")
    parser.add_argument("--no-clean", action="store_true", help="Désactiver le nettoyage")
    parser.add_argument("--no-report", action="store_true", help="Désactiver le rapport d'exécution")
    parser.add_argument("--max-models", type=int, default=None, help="Nombre max de modèles à scraper")
    args = parser.parse_args()

    scrape(
        output_dir=args.output_dir,
        apply_cleaning=not args.no_clean,
        emit_run_report=not args.no_report,
        max_models=args.max_models,
    )


if __name__ == "__main__":
    main()
