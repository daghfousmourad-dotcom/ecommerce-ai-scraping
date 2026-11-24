"""Phon'Ethik Lyon Scraper (https://www.phon-ethik.fr).

Atelier de réparation smartphone éco-responsable (Lyon Part-Dieu).
Stratégie ordonnée (Roadmap E-commerce AI Scraping) :
  1. API Directe / JSON Embedded State : Les tarifs complets sont intégrés dans la page
     sous la variable JavaScript ``window.ITELIER_PRICE_DATA`` (910+ produits, 730 modèles smartphones).
  2. Scraping HTML direct ultra-rapide (HTTP session + JSON parser) :
     Permet d'extraire l'intégralité des 6 700+ tarifs smartphone en moins de 2 secondes.
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

TARIFS_URL = "https://www.phon-ethik.fr/pages/tarifs-reparation.html"
SOURCE_NAME = "phonethik"
SOURCE_DOMAIN = "phon-ethik.fr"

SHOP_INFO = {
    "shop_name": "Phon'Ethik Lyon Part-Dieu",
    "address": "138 cours Lafayette",
    "city": "Lyon",
    "postal_code": "69003",
    "delay": "30 minutes en atelier",
    "warranty": "Garantie pièce + main d'œuvre",
}


def _clean_brand_and_model(brand_raw: str, model_raw: str) -> tuple[str, str]:
    """Nettoie et harmonise les noms de marque et de modèle."""
    brand = brand_raw.strip()
    model = model_raw.strip()

    if brand == "Google Pixel":
        brand = "Google"
    elif brand == "Autres":
        if model.lower().startswith("zebra"):
            brand = "Zebra"
            model = re.sub(r"^zebra\s*", "", model, flags=re.I).strip()
        elif any(kw in model.lower() for kw in ["données", "usine", "nettoyage", "désoxydation", "desoxydation", "transfert"]):
            brand = "Générique"
        else:
            brand = "Autres"

    return brand, model


def fetch_tarifs_catalog(session=None) -> dict[str, Any]:
    """Récupère le catalogue complet des tarifs depuis la page Phon'Ethik."""
    if session is None:
        session = create_session()

    print(f"[{SOURCE_NAME}] Téléchargement de la grille tarifaire : {TARIFS_URL}...", flush=True)
    resp = session.get(TARIFS_URL, timeout=20)
    resp.raise_for_status()
    html = resp.content.decode("utf-8", errors="replace")

    # Extraction du JSON intégré window.ITELIER_PRICE_DATA
    match = re.search(
        r"window\.ITELIER_PRICE_DATA\s*=\s*(\{.*?\});\s*(?:</script>|\n\s*//|\n\s*window)",
        html,
        re.DOTALL,
    )
    if not match:
        # Recherche tolérante en cas de variation de mise en forme
        start_idx = html.find("window.ITELIER_PRICE_DATA =")
        if start_idx != -1:
            brace_start = html.find("{", start_idx)
            # Trouver la fermeture correspondante ou la fin de balise script
            script_end = html.find("</script>", brace_start)
            json_candidate = html[brace_start:script_end].strip().rstrip(";")
            return json.loads(json_candidate)
        raise ValueError("Impossible de localiser window.ITELIER_PRICE_DATA dans le code HTML.")

    return json.loads(match.group(1))


def scrape(
    *,
    output_dir: str = str(_SCRAPERS_ROOT / "outputs"),
    apply_cleaning: bool = True,
    emit_run_report: bool = True,
    max_models: int | None = None,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    """Point d'entrée principal du scraper Phon'Ethik."""
    scrape_started_at = now_iso_utc()
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    timestamp = re.sub(r"[-:T]", "", scrape_started_at.split(".")[0]) + "0000"
    stem = f"phonethik_repair_prices_{timestamp}"

    json_path = out_dir / f"{stem}.json"
    jsonl_path = out_dir / f"{stem}.jsonl"
    csv_path = out_dir / f"{stem}.csv"

    session = create_session()
    all_rows: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    fetch_errors = 0

    try:
        data = fetch_tarifs_catalog(session)
    except Exception as e:
        print(f"[{SOURCE_NAME}] Erreur lors de la récupération des tarifs: {e}", flush=True)
        fetch_errors += 1
        issues.append(
            fetch_issue(
                reason=REASON_FETCH_ERROR,
                source_site=SOURCE_NAME,
                source_url=TARIFS_URL,
                detail=str(e),
            )
        )
        return []

    catalog = data.get("catalog", {})
    smartphones_catalog = catalog.get("Smartphone", {})
    print(f"[{SOURCE_NAME}] Catalogue chargé : {len(smartphones_catalog)} marques de smartphones.", flush=True)

    scraped_at = now_iso_utc()
    models_processed = 0

    for raw_brand, models_dict in smartphones_catalog.items():
        if not isinstance(models_dict, dict):
            continue

        for raw_model, repairs_dict in models_dict.items():
            if max_models and models_processed >= max_models:
                break
            if not isinstance(repairs_dict, dict):
                continue

            models_processed += 1
            brand, model = _clean_brand_and_model(raw_brand, raw_model)
            brand_slug = re.sub(r"[^a-zA-Z0-9]+", "-", brand.lower()).strip("-")
            model_slug = re.sub(r"[^a-zA-Z0-9]+", "-", model.lower()).strip("-")
            model_url = f"{TARIFS_URL}#{brand_slug}/{model_slug}"

            for repair_label, raw_val in repairs_dict.items():
                if raw_val is None:
                    # Pièce non disponible ou devis sur demande
                    continue

                price_val: float | None = None
                price_raw: str = ""

                if isinstance(raw_val, (int, float)):
                    price_val = float(raw_val)
                    price_raw = f"{price_val:.2f} €"
                elif isinstance(raw_val, str):
                    pv, _ = parse_price_and_currency(raw_val)
                    price_val = pv
                    price_raw = raw_val.strip()

                if price_val is None or price_val <= 0:
                    continue

                canon_repair, is_matched = normalize_repair_type(repair_label)
                repair_type = canon_repair if canon_repair else repair_label

                all_rows.append(
                    {
                        "source_site": SOURCE_DOMAIN,
                        "source_url": model_url,
                        "source": model_url,
                        "timestamp": scraped_at,
                        "data_type": "repair_price",
                        "device_family": "Smartphone",
                        "brand": brand,
                        "model": model,
                        "item_name": repair_label,
                        "repair_type_raw": repair_label,
                        "repair_type": repair_type,
                        "repair_type_matched": bool(is_matched),
                        "price": price_val,
                        "currency": "EUR",
                        "price_raw": price_raw,
                        "delay": SHOP_INFO["delay"],
                        "warranty": SHOP_INFO["warranty"],
                        "shop_name": SHOP_INFO["shop_name"],
                        "address": SHOP_INFO["address"],
                        "city": SHOP_INFO["city"],
                        "postal_code": SHOP_INFO["postal_code"],
                        "scrape_started_at": scrape_started_at,
                    }
                )

    print(
        f"[{SOURCE_NAME}] Extraction brute terminée : {models_processed} modèles, {len(all_rows)} tarifs avec prix.",
        flush=True,
    )

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

    print(f"\n[PhonEthik] Terminé avec succès. {len(all_rows)} tarifs exportés dans {out_dir}", flush=True)

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
    parser = argparse.ArgumentParser(description="Phon'Ethik Lyon Scraper")
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
