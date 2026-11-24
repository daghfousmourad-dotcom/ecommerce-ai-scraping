"""Phone Express Scraper (https://phoneexpress.fr/tarifs).

Grille tarifaire structurée de réparation smartphones et tablettes à Martigues :
Stratégie ordonnée (Roadmap E-commerce AI Scraping) :
  2. Parsing HTML Direct (BeautifulSoup / Requests) :
     - Téléchargement direct de https://phoneexpress.fr/tarifs
     - Extraction des 4 tables HTML exhaustives : iPhone, Samsung Galaxy, Marques Android (Xiaomi, Huawei, OnePlus, Pixel, Oppo), et Tablettes (iPad, Galaxy Tab)
     - Expansion des modèles groupés (ex: 'iPhone 8 / 8 Plus' -> 'iPhone 8', 'iPhone 8 Plus')
     - Normalisation des pannes constructeurs (Écran, Batterie, Connecteur, Caméra Arrière, Vitre Arrière)
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
import json
import re
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

from scraper.core.filtering import CleaningConfig, clean_dataset
from scraper.core.http_client import create_session
from scraper.core.normalization import normalize_repair_type
from scraper.core.parser_utils import (
    dedupe_dict_rows,
    now_iso_utc,
)
from scraper.core.row_export import write_jsonl_and_csv
from scraper.core.run_report import (
    REASON_FETCH_ERROR,
    fetch_issue,
    finalize_run_artifacts,
    summarize_run_line,
)

BASE_PAGE_URL = "https://phoneexpress.fr/tarifs"
SOURCE_NAME = "phone_express"
SOURCE_DOMAIN = "phoneexpress.fr"

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

SHOP_INFO = {
    "shop_name": "Phone Express",
    "address": "15 cours du 4 Septembre",
    "city": "Martigues",
    "postal_code": "13500",
    "delay": "Réparation express en 30 minutes sur place",
    "warranty": "Garantie pièces et main d'œuvre",
}

REPAIR_COLUMN_NORMALIZATION = {
    "écran": "Ecran LCD",
    "écran / vitre": "Ecran LCD",
    "vitre": "Ecran LCD",
    "batterie": "Batterie",
    "connecteur": "Connecteur de charge",
    "caméra arrière": "Appareil photo arrière",
    "vitre arrière": "Façade arrière",
}


def expand_model_names(raw_name: str, brand: str) -> list[str]:
    """Développe les libellés combinés comme 'iPhone 8 / 8 Plus' en modèles distincts."""
    raw_name = raw_name.strip()
    if " / " not in raw_name:
        return [raw_name]

    parts = [p.strip() for p in raw_name.split(" / ")]
    first = parts[0]
    results = [first]

    # Détection du préfixe récurrent (ex: 'iPhone', 'Galaxy', 'Huawei', 'iPad')
    words = first.split()
    prefix = " ".join(words[:-1]) if len(words) > 1 else ""

    known_brands = ["iPhone", "Galaxy", "Huawei", "Xiaomi", "Redmi", "iPad", "OnePlus", "Pixel", "Oppo"]

    for p in parts[1:]:
        if any(b in p for b in known_brands):
            results.append(p)
        else:
            if prefix and not p.startswith(prefix):
                results.append(f"{prefix} {p}")
            else:
                results.append(p)

    return results


def detect_brand_and_family(model_name: str, table_index: int) -> tuple[str, str]:
    """Détecte avec précision la marque et la famille d'appareil."""
    m_lower = model_name.lower()

    if table_index == 3 or "ipad" in m_lower or "tab" in m_lower:
        device_family = "Tablette"
    else:
        device_family = "Smartphone"

    if any(k in m_lower for k in ["iphone", "ipad", "apple"]):
        brand = "Apple"
    elif any(k in m_lower for k in ["galaxy", "samsung"]):
        brand = "Samsung"
    elif "huawei" in m_lower:
        brand = "Huawei"
    elif any(k in m_lower for k in ["xiaomi", "redmi"]):
        brand = "Xiaomi"
    elif "oneplus" in m_lower:
        brand = "OnePlus"
    elif "pixel" in m_lower:
        brand = "Google"
    elif "oppo" in m_lower:
        brand = "Oppo"
    else:
        brand = "Apple" if table_index in (0, 3) else ("Samsung" if table_index == 1 else "Autre")

    return brand, device_family


def scrape(
    *,
    output_dir: str = str(_SCRAPERS_ROOT / "outputs"),
    apply_cleaning: bool = True,
    emit_run_report: bool = True,
    max_models: int | None = None,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    """Point d'entrée principal du scraper Phone Express."""
    scrape_started_at = now_iso_utc()
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    timestamp = re.sub(r"[-:T]", "", scrape_started_at.split(".")[0]) + "0000"
    stem = f"phone_express_repair_prices_{timestamp}"

    json_path = out_dir / f"{stem}.json"
    jsonl_path = out_dir / f"{stem}.jsonl"
    csv_path = out_dir / f"{stem}.csv"

    session = create_session()
    all_rows: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    fetch_errors = 0

    print(f"[{SOURCE_NAME}] Téléchargement direct des grilles tarifaires {BASE_PAGE_URL}...", flush=True)
    try:
        resp = session.get(BASE_PAGE_URL, headers=DEFAULT_HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"[{SOURCE_NAME}] Erreur lors du fetch de {BASE_PAGE_URL}: {e}", flush=True)
        fetch_errors += 1
        issues.append(
            fetch_issue(
                reason=REASON_FETCH_ERROR,
                source_site=SOURCE_NAME,
                source_url=BASE_PAGE_URL,
                detail=str(e),
            )
        )
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    tables = soup.find_all("table")
    print(f"[{SOURCE_NAME}] {len(tables)} tables tarifaires identifiées sur la page.", flush=True)

    scraped_at = now_iso_utc()
    models_seen = set()

    for t_idx, table in enumerate(tables):
        headers = [th.get_text(strip=True) for th in table.find_all("th")]
        if not headers or len(headers) < 2:
            continue

        repair_cols = headers[1:]

        for row in table.find_all("tr")[1:]:
            cells = [td.get_text(strip=True) for td in row.find_all("td")]
            if len(cells) < len(headers):
                continue

            raw_model_str = cells[0]
            if not raw_model_str or raw_model_str == "—":
                continue

            initial_brand, _ = detect_brand_and_family(raw_model_str, t_idx)
            sub_models = expand_model_names(raw_model_str, initial_brand)

            for model_name in sub_models:
                brand, device_family = detect_brand_and_family(model_name, t_idx)
                models_seen.add(model_name)

                if max_models and len(models_seen) > max_models:
                    continue

                for col_idx, col_header in enumerate(repair_cols):
                    cell_val = cells[col_idx + 1]
                    if not cell_val or cell_val in ("—", "-", "N/A", "Sur devis"):
                        continue

                    # Extraction du montant numérique
                    price_match = re.search(r"(\d+(?:[.,]\d+)?)", cell_val.replace(" ", ""))
                    if not price_match:
                        continue

                    price_val = float(price_match.group(1).replace(",", "."))
                    if price_val <= 0:
                        continue

                    # Normalisation du type de panne
                    h_lower = col_header.lower().strip()
                    if h_lower in REPAIR_COLUMN_NORMALIZATION:
                        repair_type = REPAIR_COLUMN_NORMALIZATION[h_lower]
                    else:
                        canon, matched = normalize_repair_type(col_header)
                        repair_type = canon if matched and canon else col_header

                    all_rows.append(
                        {
                            "source_site": SOURCE_DOMAIN,
                            "source_url": BASE_PAGE_URL,
                            "source": BASE_PAGE_URL,
                            "timestamp": scraped_at,
                            "data_type": "repair_price",
                            "device_family": device_family,
                            "brand": brand,
                            "model": model_name.strip(),
                            "item_name": f"{col_header.strip()} {model_name.strip()}",
                            "repair_type_raw": col_header.strip(),
                            "repair_type": repair_type,
                            "price": price_val,
                            "price_raw": f"{price_val} €",
                            "currency": "EUR",
                            "currency_symbol": "€",
                            "shop_name": SHOP_INFO["shop_name"],
                            "address": SHOP_INFO["address"],
                            "city": SHOP_INFO["city"],
                            "postal_code": SHOP_INFO["postal_code"],
                            "delay": SHOP_INFO["delay"],
                            "warranty": SHOP_INFO["warranty"],
                            "model_sku": "",
                            "repair_category": "Réparation en boutique",
                            "scraped_at": scraped_at,
                        }
                    )

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

    print(f"\n[PHONE EXPRESS] Terminé avec succès. {len(deduped_rows)} tarifs exportés dans {out_dir}", flush=True)

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
    parser = argparse.ArgumentParser(description="Phone Express Scraper")
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
