"""URGENCE PHONE Scraper (https://www.urgence-phone.fr/tarifs-reparations/).

Réseau de centres de réparation smartphones dans le Var (83) / Région PACA :
1. Toulon Centre : 7 Rue d'Astour, 83000 Toulon (09 81 48 65 00)
2. La Seyne-sur-Mer : 11 Place Esprit Martel, 83500 La Seyne-sur-Mer (09 83 99 41 63)
3. Solliès-Pont : CC Intermarché, ZAC La Poulasse, Avenue de l'Arlésienne, 83210 Solliès-Pont (09 87 10 83 84)

Garantie pièces et main-d'œuvre | Prise en charge immédiate sans rendez-vous | Pièces certifiées originales constructeur

Stratégie d'extraction optimale (Stratégie 1 - API Google Sheets GViz Direct) :
    1. Interrogation directe de l'API Google Sheets GViz exportant les données du tableur officiel :
       Spreadsheet ID : 1AT84nbwIXQqUZLu3bSiOKkZmdmyVcB5kGBLYBs98Smw
    2. Extraction des 9 feuilles tarifaires actives :
       - tarifs-iphone (Apple)
       - tarifs-samsung-s (Samsung Galaxy S)
       - tarifs-samsung-note (Samsung Galaxy Note)
       - tarifs-samsung-a (Samsung Galaxy A)
       - tarifs-huawei-p (Huawei P series)
       - tarifs-huawei-mate (Huawei Mate series)
       - tarifs-oppo-a (Oppo série A)
       - tarifs-oppo-find (Oppo série Find)
       - tarifs-oppo-reno (Oppo série Reno)
    3. Normalisation standard 22 colonnes E-commerce AI V2 pour les 3 ateliers PACA.
    4. Nettoyage E-commerce AI V2 et export certifié des 5 artefacts standard.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import ssl
import sys
from pathlib import Path
from typing import Any
import urllib.request

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

SOURCE_NAME = "urgence_phone"
SOURCE_DOMAIN = "urgence-phone.fr"
SOURCE_PAGE_URL = "https://www.urgence-phone.fr/tarifs-reparations/"

SPREADSHEET_ID = "1AT84nbwIXQqUZLu3bSiOKkZmdmyVcB5kGBLYBs98Smw"
GVIZ_BASE_URL = f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/gviz/tq?tqx=out:csv&sheet="

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)

STORES = [
    {
        "id": "toulon",
        "shop_name": "URGENCE PHONE - Toulon Centre",
        "address": "7 Rue d'Astour",
        "city": "Toulon",
        "postal_code": "83000",
        "phone": "09 81 48 65 00",
    },
    {
        "id": "la_seyne",
        "shop_name": "URGENCE PHONE - La Seyne-sur-Mer",
        "address": "11 Place Esprit Martel",
        "city": "La Seyne-sur-Mer",
        "postal_code": "83500",
        "phone": "09 83 99 41 63",
    },
    {
        "id": "sollies",
        "shop_name": "URGENCE PHONE - Solliès-Pont",
        "address": "CC Intermarché, ZAC La Poulasse, Avenue de l'Arlésienne",
        "city": "Solliès-Pont",
        "postal_code": "83210",
        "phone": "09 87 10 83 84",
    },
]

TARGET_SHEETS = [
    {"sheet": "tarifs-iphone", "brand": "Apple", "device_family": "Smartphone"},
    {"sheet": "tarifs-samsung-s", "brand": "Samsung", "device_family": "Smartphone"},
    {"sheet": "tarifs-samsung-note", "brand": "Samsung", "device_family": "Smartphone"},
    {"sheet": "tarifs-samsung-a", "brand": "Samsung", "device_family": "Smartphone"},
    {"sheet": "tarifs-huawei-p", "brand": "Huawei", "device_family": "Smartphone"},
    {"sheet": "tarifs-huawei-mate", "brand": "Huawei", "device_family": "Smartphone"},
    {"sheet": "tarifs-oppo-a", "brand": "Oppo", "device_family": "Smartphone"},
    {"sheet": "tarifs-oppo-find", "brand": "Oppo", "device_family": "Smartphone"},
    {"sheet": "tarifs-oppo-reno", "brand": "Oppo", "device_family": "Smartphone"},
]

HEADER_MAPPING: dict[str, tuple[str, str]] = {
    # col_header: (raw_label, canonical_ecommerce_ai_scraping)
    "LCD": ("Écran LCD", "Ecran LCD"),
    "OLED": ("Écran OLED", "Ecran LCD"),
    "BATTERIE": ("Batterie", "Batterie"),
    "VITRE ARRIERE": ("Vitre arrière", "Façade arrière"),
    "CONNECTEUR": ("Connecteur de charge", "Connecteur de charge"),
}


def fetch_sheet_csv(sheet_name: str) -> list[list[str]] | None:
    """Télécharge une feuille spécifique via l'API Google Sheets GViz CSV."""
    url = f"{GVIZ_BASE_URL}{sheet_name}"
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=12) as resp:
            content = resp.read().decode("utf-8", errors="ignore")
            reader = csv.reader(io.StringIO(content))
            return list(reader)
    except Exception as exc:
        print(f"[{SOURCE_NAME.upper()}] ⚠️ Erreur lors du chargement de la feuille {sheet_name}: {exc}")
        return None


def format_model_name(raw_model: str, brand: str, sheet_name: str) -> str:
    """Normalise et préfixe proprement le modèle."""
    m = raw_model.strip()
    if brand == "Apple":
        if not m.lower().startswith("iphone"):
            m = f"iPhone {m}"
    elif brand == "Samsung":
        if not m.lower().startswith("galaxy"):
            m = f"Galaxy {m}"
    elif brand == "Oppo":
        if "find" in sheet_name and not m.lower().startswith("find"):
            m = f"Find {m}"
        elif "reno" in sheet_name and not m.lower().startswith("reno"):
            m = f"Reno {m}"
    return m.strip()


def parse_numeric_price(price_str: str) -> float | None:
    """Extrait le prix numérique d'une chaîne (ex: '129€', '59 €', '49')."""
    cleaned = price_str.replace(" ", "").replace("\u00a0", "").replace(",", ".")
    match = re.search(r"(\d+(?:\.\d+)?)", cleaned)
    if match:
        try:
            val = float(match.group(1))
            return val if val > 0 else None
        except ValueError:
            return None
    return None


def create_repair_row(
    store: dict[str, str],
    brand: str,
    model: str,
    device_family: str,
    repair_type_raw: str,
    canonical_repair: str,
    price: float,
    scraped_at: str,
) -> dict[str, Any]:
    """Construit une ligne standard 22 colonnes E-commerce AI V2."""
    price_raw_str = f"{int(price) if price.is_integer() else price} €"
    item_name = f"{repair_type_raw} {brand} {model}".strip()

    return {
        "source_site": SOURCE_DOMAIN,
        "source_url": SOURCE_PAGE_URL,
        "source": SOURCE_PAGE_URL,
        "timestamp": scraped_at,
        "data_type": "repair_price",
        "device_family": device_family,
        "brand": brand,
        "model": model,
        "item_name": item_name,
        "repair_type_raw": repair_type_raw,
        "repair_type": canonical_repair,
        "price": price,
        "price_raw": price_raw_str,
        "currency": "EUR",
        "currency_symbol": "€",
        "shop_name": store["shop_name"],
        "address": store["address"],
        "city": store["city"],
        "postal_code": store["postal_code"],
        "delay": "Réparation express en moins d'une heure sans rendez-vous",
        "warranty": "Garantie pièces et main-d'œuvre, pièces certifiées originales constructeur",
        "model_sku": "",
        "repair_category": "Réparation en atelier",
        "scraped_at": scraped_at,
    }


def scrape(
    output_dir: str | Path | None = None,
    apply_cleaning: bool = True,
    emit_run_report: bool = True,
    max_models: int | None = None,
) -> list[dict[str, Any]]:
    scrape_started_at = now_iso_utc()
    out_dir = Path(output_dir) if output_dir else (_SCRAPERS_ROOT / "outputs")
    out_dir.mkdir(parents=True, exist_ok=True)

    prefix = f"{SOURCE_NAME}_repair_price_{scrape_started_at}"
    jsonl_path = out_dir / f"{prefix}.jsonl"
    csv_path = out_dir / f"{prefix}.csv"
    json_path = out_dir / f"{prefix}.json"

    fetch_errors: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    all_rows: list[dict[str, Any]] = []

    print(f"\n[{SOURCE_NAME.upper()}] 🌐 Démarrage du scraper URGENCE PHONE (Toulon, La Seyne, Solliès)", flush=True)
    print(f"[{SOURCE_NAME.upper()}] 📊 Connexion API Google Sheets GViz : Spreadsheet ID {SPREADSHEET_ID}", flush=True)

    total_models_processed = 0

    for sheet_cfg in TARGET_SHEETS:
        sheet_name = sheet_cfg["sheet"]
        brand = sheet_cfg["brand"]
        family = sheet_cfg["device_family"]

        rows = fetch_sheet_csv(sheet_name)
        if not rows:
            fetch_errors.append(fetch_issue(f"{GVIZ_BASE_URL}{sheet_name}", REASON_FETCH_ERROR, f"Échec requête feuille {sheet_name}"))
            continue

        headers = [h.strip().upper() for h in rows[0]]
        sheet_prices = 0

        for r in rows[1:]:
            if not r or not r[0].strip():
                continue

            if max_models and total_models_processed >= max_models:
                break
            total_models_processed += 1

            raw_model = r[0].strip()
            model_clean = format_model_name(raw_model, brand, sheet_name)

            for col_idx in range(1, len(headers)):
                if col_idx >= len(r):
                    continue

                col_name = headers[col_idx]
                if not col_name or col_name not in HEADER_MAPPING:
                    continue

                price_raw = r[col_idx].strip()
                if not price_raw or price_raw in ("-", "—", "/"):
                    continue

                price_val = parse_numeric_price(price_raw)
                if not price_val:
                    continue

                sheet_prices += 1
                raw_label, canon_rep = HEADER_MAPPING[col_name]

                # Génération pour les 3 ateliers du Var
                for store in STORES:
                    row = create_repair_row(
                        store=store,
                        brand=brand,
                        model=model_clean,
                        device_family=family,
                        repair_type_raw=raw_label,
                        canonical_repair=canon_rep,
                        price=price_val,
                        scraped_at=scrape_started_at,
                    )
                    all_rows.append(row)

        print(f"[{SOURCE_NAME.upper()}] 📋 Feuille {sheet_name} ({brand}) : {sheet_prices} tarifs unitaires extraits", flush=True)

    print(f"[{SOURCE_NAME.upper()}] 🔄 Données assemblées : {len(all_rows)} tarifs au total pour les 3 ateliers", flush=True)

    # Déduplication
    deduped_rows = dedupe_dict_rows(
        all_rows,
        keys=["model", "repair_type_raw", "price", "shop_name"],
    )

    # Nettoyage E-commerce AI V2
    clean_report = None
    if apply_cleaning:
        cleaning_cfg = CleaningConfig(
            price_min=5.0,
            price_max=4000.0,
            dedupe_keys_repair_price=("shop_name", "model", "repair_type_raw", "price_raw"),
        )
        deduped_rows, clean_report = clean_dataset(
            deduped_rows,
            data_type="repair_price",
            config=cleaning_cfg,
            cleaning_issues=issues if emit_run_report else None,
        )
        if clean_report:
            print(clean_report.summary_line(), flush=True)

    for row in deduped_rows:
        row.setdefault("scrape_started_at", scrape_started_at)

    # Exportation des artefacts E-commerce AI V2
    write_jsonl_and_csv(deduped_rows, jsonl_path=jsonl_path, csv_path=csv_path)

    payload = {
        "source_site": SOURCE_NAME,
        "scrape_started_at": scrape_started_at,
        "row_count": len(deduped_rows),
        "rows": deduped_rows,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n[{SOURCE_NAME.upper()}] ✅ Succès : {len(deduped_rows)} tarifs exportés dans {out_dir}", flush=True)

    if emit_run_report:
        payload_report = finalize_run_artifacts(
            source_site=SOURCE_NAME,
            scrape_started_at=scrape_started_at,
            json_path=json_path,
            jsonl_path=jsonl_path,
            csv_path=csv_path,
            row_count=len(deduped_rows),
            apply_cleaning=apply_cleaning,
            clean_report=clean_report,
            fetch_errors=fetch_errors,
            issues=issues,
        )
        print(summarize_run_line(payload_report), flush=True)

    return deduped_rows


def main() -> None:
    parser = argparse.ArgumentParser(description="URGENCE PHONE Scraper (Toulon, La Seyne, Solliès)")
    parser.add_argument("--output-dir", default=str(_SCRAPERS_ROOT / "outputs"), help="Dossier de sortie")
    parser.add_argument("--no-clean", action="store_true", help="Désactiver le nettoyage")
    parser.add_argument("--no-report", action="store_true", help="Désactiver le rapport d'exécution")
    parser.add_argument("--max-models", type=int, default=None, help="Nombre max de modèles extraits")
    args = parser.parse_args()

    scrape(
        output_dir=args.output_dir,
        apply_cleaning=not args.no_clean,
        emit_run_report=not args.no_report,
        max_models=args.max_models,
    )


if __name__ == "__main__":
    main()
