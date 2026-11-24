"""DBC MARSEILLE Scraper (https://dbcstore.fr).

Boutiques spécialisées de vente, rachat et réparation smartphone à Marseille :
- Implantation : 5 Rue Grignan (13006 Marseille) et CC Grand V (13011 Marseille)
- Univers : product_sales (Smartphones, tablettes, Mac/laptops, montres connectées reconditionnés)
- Architecture : Next.js App Router sur Vercel avec catalogue structuré.

Stratégie d'extraction (Niveau 2 & 4 — Catégories Next.js & Parsing des Cartes Produits) :
    1. Itération sur les univers produits (/smartphones/apple, /smartphones/samsung, /tablets, /laptops, /smartwatches)
    2. Extraction des cartes produits .group
    3. Parsing : Modèle, marque, prix de départ boutique TTC, prix de référence neuf, disponibilité
    4. Normalisation standard E-commerce AI V2 (product_sales)
    5. Exportation certifiée des 5 artefacts standard (.jsonl, .csv, .json, _run.json, _issues.jsonl).
"""

from __future__ import annotations

import argparse
import json
import re
import ssl
import sys
import urllib.request
from pathlib import Path
from typing import Any
from bs4 import BeautifulSoup

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
    from core.parser_utils import dedupe_dict_rows, now_iso_utc
    from core.row_export import write_jsonl_and_csv
    from core.run_report import (
        REASON_FETCH_ERROR,
        fetch_issue,
        finalize_run_artifacts,
        summarize_run_line,
    )

BASE_URL = "https://dbcstore.fr"
SOURCE_NAME = "dbc_marseille"
SOURCE_DOMAIN = "dbcstore.fr"

CATEGORIES = [
    ("/fr/products/smartphones/apple", "Apple", "smartphone", "Smartphone"),
    ("/fr/products/smartphones/samsung", "Samsung", "smartphone", "Smartphone"),
    ("/fr/products/tablets", "Apple", "tablette", "Tablette"),
    ("/fr/products/laptops", "Apple", "ordinateur", "Ordinateur"),
    ("/fr/products/smartwatches", "Apple", "smartwatch", "Montre connectée"),
]

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)

STORAGE_RE = re.compile(r"(\d+)\s*(?:Go|GB|To|TB)", re.I)


def scrape(
    output_dir: str | Path | None = None,
    apply_cleaning: bool = True,
    emit_run_report: bool = True,
    max_models: int | None = None,
) -> list[dict[str, Any]]:
    scrape_started_at = now_iso_utc()
    out_dir = Path(output_dir) if output_dir else (_SCRAPERS_ROOT / "outputs")
    out_dir.mkdir(parents=True, exist_ok=True)

    timestamp_clean = scrape_started_at.replace(":", "").replace("-", "").replace("+", "")
    prefix = f"{SOURCE_NAME}_product_sales_{timestamp_clean}"
    jsonl_path = out_dir / f"{prefix}.jsonl"
    csv_path = out_dir / f"{prefix}.csv"
    json_path = out_dir / f"{prefix}.json"
    run_path = out_dir / f"{prefix}_run.json"
    issues_path = out_dir / f"{prefix}_issues.jsonl"

    convenience_jsonl = out_dir / f"db_{SOURCE_NAME}.jsonl"
    convenience_csv = out_dir / f"db_{SOURCE_NAME}.csv"
    convenience_json = out_dir / f"db_{SOURCE_NAME}.json"

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "fr-FR,fr;q=0.9",
    }

    issues: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    print(f"[{SOURCE_NAME}] Exploration des univers DBC Store Marseille...")

    for path_cat, default_brand, dev_type, dev_family in CATEGORIES:
        cat_url = f"{BASE_URL}{path_cat}"
        try:
            req = urllib.request.Request(cat_url, headers=headers)
            with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
                html = resp.read().decode("utf-8", errors="ignore")
        except Exception as e:
            print(f"[{SOURCE_NAME}] Erreur sur {cat_url}: {e}")
            issues.append(fetch_issue(source_site=SOURCE_NAME, url=cat_url, reason=REASON_FETCH_ERROR, message=str(e)))
            continue

        soup = BeautifulSoup(html, "html.parser")
        cards = soup.select('a[href*="/products/"]')

        cat_count = 0
        for card in cards:
            href = card.get("href", "")
            if not href or href in seen_urls:
                continue

            full_url = f"{BASE_URL}{href}" if not href.startswith("http") else href

            # Nom du modèle
            h3 = card.select_one("h3")
            if not h3:
                continue
            model_name = h3.get_text(strip=True)

            # Prix
            price_val: float | None = None
            price_el = card.select_one(".text-lg.font-bold, .font-bold.text-gray-900")
            if price_el:
                txt = price_el.get_text(strip=True).replace(" ", " ").replace(" ", "").replace("€", "").replace(",", ".").strip()
                try:
                    price_val = float(txt)
                except ValueError:
                    price_val = None

            if not price_val:
                # Fallback regex
                card_text = card.get_text(" ", strip=True)
                m = re.search(r"À partir de\s*(\d+[\s ]?\d*)\s*€", card_text)
                if m:
                    clean_p = m.group(1).replace(" ", "").replace(" ", "").replace(",", ".")
                    try:
                        price_val = float(clean_p)
                    except ValueError:
                        price_val = None

            if not price_val or price_val <= 0:
                continue

            seen_urls.add(href)
            cat_count += 1

            # Marque
            brand = default_brand
            if "iphone" in model_name.lower() or "macbook" in model_name.lower() or "ipad" in model_name.lower() or "apple" in model_name.lower():
                brand = "Apple"
            elif "galaxy" in model_name.lower() or "samsung" in model_name.lower():
                brand = "Samsung"

            # Stockage
            storage_gb: int | None = None
            storage_raw: str | None = None
            sm = STORAGE_RE.search(model_name)
            if sm:
                storage_raw = sm.group(0).strip()
                try:
                    storage_gb = int(sm.group(1))
                except ValueError:
                    pass

            avail = "InStock" if "En stock" in card.get_text() else "PreOrder"

            row = {
                "source_site": SOURCE_DOMAIN,
                "source_url": full_url,
                "source": full_url,
                "timestamp": scrape_started_at,
                "scrape_started_at": scrape_started_at,
                "data_type": "product_sale",
                "device_type": dev_type,
                "device_family": dev_family,
                "brand": brand,
                "model": model_name,
                "model_normalized": f"{brand} {model_name}".strip(),
                "item_title": f"{brand} {model_name} Reconditionné",
                "storage_gb": storage_gb,
                "storage_raw": storage_raw,
                "condition": "reconditionne",
                "product_id": href.split("/")[-1],
                "price": price_val,
                "price_raw": f"{price_val:.2f} €".replace(".", ","),
                "currency": "EUR",
                "currency_symbol": "€",
                "seller_name": "DBC Store Marseille (Grignan & Grand V)",
                "shipping_cost": 0.0,
                "warranty": "Garantie 12 à 24 mois atelier Marseille",
                "availability": avail,
                "scraped_at": scrape_started_at,
            }
            rows.append(row)

            if max_models and len(rows) >= max_models:
                break

        print(f"[{SOURCE_NAME}] Univers {path_cat}: {cat_count} produits extraits (Total: {len(rows)})")
        if max_models and len(rows) >= max_models:
            break

    initial_count = len(rows)
    rows = dedupe_dict_rows(rows, keys=("source_site", "brand", "model", "product_id", "price"))
    print(f"[{SOURCE_NAME}] Total lignes brutes: {initial_count}, après déduplication: {len(rows)}")

    clean_report = None
    fetch_errors = len(issues)
    if apply_cleaning:
        cleaning_cfg = CleaningConfig(
            price_min=10.0,
            price_max=4000.0,
            dedupe_keys_product_sale=("source_url", "product_id", "price_raw"),
        )
        rows, clean_report = clean_dataset(
            rows,
            data_type="product_sale",
            config=cleaning_cfg,
            cleaning_issues=issues if emit_run_report else None,
        )
        if clean_report:
            print(clean_report.summary_line(), flush=True)

    for row in rows:
        row.setdefault("scrape_started_at", scrape_started_at)

    write_jsonl_and_csv(rows, jsonl_path=jsonl_path, csv_path=csv_path)

    payload = {
        "source_site": SOURCE_NAME,
        "scrape_started_at": scrape_started_at,
        "row_count": len(rows),
        "rows": rows,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # Convenience files
    try:
        import shutil
        shutil.copy(jsonl_path, convenience_jsonl)
        shutil.copy(csv_path, convenience_csv)
        shutil.copy(json_path, convenience_json)
    except Exception:
        pass

    print(f"[{SOURCE_NAME.upper()}] ✅ Succès : {len(rows)} offres exportées dans {out_dir}", flush=True)

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
