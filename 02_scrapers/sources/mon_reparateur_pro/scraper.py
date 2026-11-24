"""Mon Réparateur Pro Scraper (https://www.monreparateurpro.fr).

Atelier de réparation smartphones et multimédia à Clermont-Ferrand :
- 8 Place d’Espagne, 63000 Clermont-Ferrand
- Stratégie d'extraction :
    1. Scraping direct de la grille tarifaire complète (/tarifs-reparation-telephone-clermont-ferrand).
    2. Extraction des 20 tableaux de marques (Apple, Samsung Galaxy S/Note/A, Huawei P/Mate,
       Oppo A/Find/F, Google Pixel, Xiaomi, Honor, OnePlus, Realme, Motorola, Sony, Wiko, Crosscall, TCL, Vivo).
    3. Normalisation canonique des modèles et des types de réparation (Ecran, Façade arrière, Batterie).
    4. Déduplication et standardisation selon le schéma d'artefacts E-commerce AI V2.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SCRAPERS_ROOT = Path(__file__).resolve().parents[2]
if str(_SCRAPERS_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRAPERS_ROOT))
if str(_SCRAPERS_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(_SCRAPERS_ROOT.parent))
_CORE_DIR = _SCRAPERS_ROOT / "core"
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))

import argparse
import json
import re
from typing import Any

from bs4 import BeautifulSoup

try:
    from scraper.core.filtering import CleaningConfig, clean_dataset
    from scraper.core.http_client import create_session
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
    from core.http_client import create_session
    from core.normalization import normalize_repair_type
    from core.parser_utils import dedupe_dict_rows, now_iso_utc
    from core.row_export import write_jsonl_and_csv
    from core.run_report import (
        REASON_FETCH_ERROR,
        fetch_issue,
        finalize_run_artifacts,
        summarize_run_line,
    )

TARGET_URL = "https://www.monreparateurpro.fr/tarifs-reparation-telephone-clermont-ferrand"
SOURCE_NAME = "mon_reparateur_pro"
SOURCE_DOMAIN = "monreparateurpro.fr"

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
}

SHOP_INFO = {
    "shop_name": "Mon Réparateur Pro Clermont-Ferrand",
    "address": "8 Place d’Espagne",
    "city": "Clermont-Ferrand",
    "postal_code": "63000",
    "phone": "07 55 23 63 63",
    "delay": "30 minutes sans rendez-vous",
    "warranty": "Garantie 6 mois à 1 an",
}

REPAIR_COLUMN_MAP = {
    "ecran": ("Ecran", "Ecran LCD"),
    "écran": ("Ecran", "Ecran LCD"),
    "vitre arriere": ("Vitre arrière", "Façade arrière"),
    "vitre arrière": ("Vitre arrière", "Façade arrière"),
    "vitre arrère": ("Vitre arrière", "Façade arrière"),
    "batterie": ("Batterie", "Batterie"),
}


def clean_model_and_brand(raw_model: str, heading: str) -> tuple[str, str]:
    """Nettoie le nom du modèle et infère la marque canonique."""
    m = raw_model.strip()
    h = heading.upper()

    brand = "Autre"
    if "IPHONE" in h or "APPLE" in h:
        brand = "Apple"
        if not re.match(r"^(iPhone|Apple)", m, re.I):
            m = f"iPhone {m}"
    elif "SAMSUNG" in h:
        brand = "Samsung"
        if not re.match(r"^(Samsung|Galaxy)", m, re.I):
            if "NOTE" in h:
                m = f"Samsung Galaxy {m}" if not m.upper().startswith("GALAXY") else f"Samsung {m}"
            else:
                m = f"Samsung Galaxy {m}"
        elif m.upper().startswith("GALAXY"):
            m = f"Samsung {m}"
    elif "HUAWEI" in h:
        brand = "Huawei"
        if not re.match(r"^Huawei", m, re.I):
            m = f"Huawei {m}"
    elif "OPPO" in h:
        brand = "Oppo"
        if not re.match(r"^Oppo", m, re.I):
            m = f"Oppo {m}"
    elif "GOOGLE" in h or "PIXEL" in h:
        brand = "Google"
        if not re.match(r"^(Google|Pixel)", m, re.I):
            m = f"Google Pixel {m}"
        elif m.upper().startswith("PIXEL"):
            m = f"Google {m}"
    elif "XIAOMI" in h or "REDMI" in h:
        brand = "Xiaomi"
        if not re.match(r"^(Xiaomi|Redmi|Poco|Mi)", m, re.I):
            m = f"Xiaomi {m}"
    elif "HONOR" in h:
        brand = "Honor"
        m = re.sub(r"^(Honor\s*(Série\s*)?)+", "", m, flags=re.I).strip()
        m = f"Honor {m}"
    elif "ONEPLUS" in h or "ONE PLUS" in h:
        brand = "OnePlus"
        if not re.match(r"^OnePlus", m, re.I):
            m = f"OnePlus {m}"
    elif "REALME" in h:
        brand = "Realme"
        if not re.match(r"^Realme", m, re.I):
            m = f"Realme {m}"
    elif "MOTOROLA" in h:
        brand = "Motorola"
        if not re.match(r"^(Motorola|Moto)", m, re.I):
            m = f"Motorola {m}"
    elif "SONY" in h:
        brand = "Sony"
        if not re.match(r"^(Sony|Xperia)", m, re.I):
            m = f"Sony {m}"
    elif "WIKO" in h:
        brand = "Wiko"
        if not re.match(r"^Wiko", m, re.I):
            m = f"Wiko {m}"
    elif "TCL" in h:
        brand = "TCL"
        if not re.match(r"^TCL", m, re.I):
            m = f"TCL {m}"
    elif "VIVO" in h:
        brand = "Vivo"
        if not re.match(r"^Vivo", m, re.I):
            m = f"Vivo {m}"
    elif "CROSSCALL" in h:
        brand = "Crosscall"
        if not re.match(r"^Crosscall", m, re.I):
            m = f"Crosscall {m}"

    # Normalisation des espaces internes
    m = re.sub(r"\s+", " ", m).strip()
    return brand, m


def parse_price(val: str) -> float | None:
    """Parse une chaîne de prix et extrait un flottant valide ou None."""
    if not val:
        return None
    val_clean = val.lower().strip()
    if any(k in val_clean for k in ["devis", "-", "gratuit", "n/a", "non"]):
        return None
    m = re.search(r"(\d+(?:[.,]\d+)?)", val_clean)
    if m:
        try:
            p = float(m.group(1).replace(",", "."))
            if p > 0:
                return p
        except ValueError:
            pass
    return None


def scrape(
    output_dir: str = str(_SCRAPERS_ROOT / "outputs"),
    apply_cleaning: bool = True,
    emit_run_report: bool = True,
    max_models: int | None = None,
) -> list[dict[str, Any]]:
    """Point d'entrée principal du scraper Mon Réparateur Pro."""
    scrape_started_at = now_iso_utc()
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    timestamp = re.sub(r"[-:T]", "", scrape_started_at.split(".")[0]) + "0000"
    stem = f"mon_reparateur_pro_repair_prices_{timestamp}"

    json_path = out_dir / f"{stem}.json"
    jsonl_path = out_dir / f"{stem}.jsonl"
    csv_path = out_dir / f"{stem}.csv"

    print(f"[{SOURCE_NAME}] Démarrage de l'extraction sur {SOURCE_DOMAIN}...", flush=True)

    session = create_session()
    session.headers.update(DEFAULT_HEADERS)

    issues: list[dict[str, Any]] = []
    fetch_errors = 0
    raw_offers: list[dict[str, Any]] = []

    try:
        resp = session.get(TARGET_URL, timeout=20)
        if resp.status_code != 200:
            fetch_errors += 1
            issues.append(fetch_issue(TARGET_URL, f"HTTP {resp.status_code}", REASON_FETCH_ERROR))
            print(f"[{SOURCE_NAME}] Erreur HTTP {resp.status_code} sur {TARGET_URL}", flush=True)
            return []
        html_content = resp.text
    except Exception as e:
        fetch_errors += 1
        issues.append(fetch_issue(TARGET_URL, f"Erreur de connexion: {e}", REASON_FETCH_ERROR))
        print(f"[{SOURCE_NAME}] Erreur de connexion sur {TARGET_URL}: {e}", flush=True)
        return []

    soup = BeautifulSoup(html_content, "html.parser")
    tables = soup.find_all("table")

    print(f"[{SOURCE_NAME}] {len(tables)} tables de tarifs trouvées. Analyse en cours...", flush=True)

    for i, table in enumerate(tables):
        heading = table.find_previous(["h1", "h2", "h3", "h4"])
        heading_text = heading.get_text(strip=True) if heading else f"Table_{i+1}"
        rows = table.find_all("tr")
        if not rows:
            continue

        # Extraction des entêtes de colonnes
        header_cells = rows[0].find_all(["th", "td"])
        col_headers = [c.get_text(strip=True).lower() for c in header_cells]

        # Détermination de l'index des colonnes
        repair_cols: list[tuple[int, str, str]] = []
        for idx, col_h in enumerate(col_headers):
            if idx == 0:
                continue
            for map_key, (raw_type, norm_type) in REPAIR_COLUMN_MAP.items():
                if map_key in col_h:
                    repair_cols.append((idx, raw_type, norm_type))
                    break

        # Si pas de correspondance d'entête explicite, colonnes par défaut :
        if not repair_cols and len(header_cells) >= 4:
            repair_cols = [
                (1, "Ecran", "Ecran LCD"),
                (2, "Vitre arrière", "Façade arrière"),
                (3, "Batterie", "Batterie"),
            ]

        for row in rows[1:]:
            cells = row.find_all(["td", "th"])
            if len(cells) < 2:
                continue

            raw_model = cells[0].get_text(strip=True)
            if not raw_model or raw_model.lower() in ["modèle", "modele"]:
                continue

            brand, model_name = clean_model_and_brand(raw_model, heading_text)

            for col_idx, raw_repair, norm_repair in repair_cols:
                if col_idx < len(cells):
                    raw_val = cells[col_idx].get_text(strip=True)
                    price = parse_price(raw_val)
                    if price is not None:
                        raw_offers.append({
                            "brand": brand,
                            "model": model_name,
                            "item_name": f"{raw_repair} {model_name}",
                            "repair_type_raw": raw_repair,
                            "repair_type": norm_repair,
                            "price": price,
                            "price_raw": raw_val,
                            "source_url": TARGET_URL,
                        })

            if max_models and len(raw_offers) >= max_models:
                break
        if max_models and len(raw_offers) >= max_models:
            break

    print(f"[{SOURCE_NAME}] {len(raw_offers)} tarifs bruts extraits.", flush=True)

    # Standardisation des enregistrements E-commerce AI Scraping
    scraped_at = now_iso_utc()
    all_rows: list[dict[str, Any]] = []
    for item in raw_offers:
        price_val = float(item["price"])
        price_raw_str = item["price_raw"] if "€" in item["price_raw"] else f"{int(price_val)} €"

        all_rows.append({
            "source_site": SOURCE_DOMAIN,
            "source_url": item["source_url"],
            "source": item["source_url"],
            "timestamp": scraped_at,
            "data_type": "repair_price",
            "device_family": "Smartphone",
            "brand": item["brand"],
            "model": item["model"],
            "item_name": item["item_name"],
            "repair_type_raw": item["repair_type_raw"],
            "repair_type": item["repair_type"],
            "price": price_val,
            "price_raw": price_raw_str,
            "currency": "EUR",
            "currency_symbol": "€",
            "shop_name": SHOP_INFO["shop_name"],
            "address": SHOP_INFO["address"],
            "city": SHOP_INFO["city"],
            "postal_code": SHOP_INFO["postal_code"],
            "delay": SHOP_INFO["delay"],
            "warranty": SHOP_INFO["warranty"],
            "model_sku": "",
            "repair_category": "Réparation en atelier",
            "scraped_at": scraped_at,
        })

    # Déduplication
    deduped_rows = dedupe_dict_rows(
        all_rows,
        keys=["model", "repair_type_raw", "price", "shop_name"],
    )

    clean_report = None
    if apply_cleaning:
        cleaning_cfg = CleaningConfig(
            price_min=10.0,
            price_max=4000.0,
            dedupe_keys_repair_price=("source_url", "model", "repair_type_raw", "price_raw"),
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

    write_jsonl_and_csv(deduped_rows, jsonl_path=jsonl_path, csv_path=csv_path)

    payload = {
        "source_site": SOURCE_NAME,
        "scrape_started_at": scrape_started_at,
        "row_count": len(deduped_rows),
        "rows": deduped_rows,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n[MON RÉPARATEUR PRO] Terminé avec succès : {len(deduped_rows)} tarifs exportés dans {out_dir}", flush=True)

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
    parser = argparse.ArgumentParser(description="Mon Réparateur Pro Scraper (Clermont-Ferrand)")
    parser.add_argument("--output-dir", default=str(_SCRAPERS_ROOT / "outputs"), help="Dossier de sortie")
    parser.add_argument("--no-clean", action="store_true", help="Désactiver le nettoyage")
    parser.add_argument("--no-report", action="store_true", help="Désactiver le rapport d'exécution")
    parser.add_argument("--max-models", type=int, default=None, help="Nombre max d'offres extraites")
    args = parser.parse_args()

    scrape(
        output_dir=args.output_dir,
        apply_cleaning=not args.no_clean,
        emit_run_report=not args.no_report,
        max_models=args.max_models,
    )


if __name__ == "__main__":
    main()
