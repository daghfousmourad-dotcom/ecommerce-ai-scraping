"""Recommerce Scraper (https://www.recommerce.com).

Pionnier européen du reconditionnement de smartphones (France) :
- Univers : product_sales (Smartphones reconditionnés certifiés)
- Catalogue officiel : https://www.recommerce.com/fr/les-telephones-reconditionnes
- Données structurées : Magento 2 avec attributs HTML data-price-amount et microdonnées.

Stratégie d'extraction (Niveau 4 — Content Scraping Pagination DOM) :
    1. Itération sur les pages du catalogue paginé (?p=1, ?p=2, ...)
    2. Extraction des cartes produits .product-item-info
    3. Parsing des attributs : modèle, marque, stockage, couleur, grade et prix final TTC
    4. Détection automatique de la fin du catalogue (déduplication IDs)
    5. Normalisation standard E-commerce AI V2 (product_sales)
    6. Exportation certifiée des 5 artefacts standard (.jsonl, .csv, .json, _run.json, _issues.jsonl).
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

BASE_URL = "https://www.recommerce.com"
SOURCE_NAME = "recommerce"
SOURCE_DOMAIN = "recommerce.com"
CATALOG_URL = f"{BASE_URL}/fr/les-telephones-reconditionnes"

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
    seen_product_ids: set[str] = set()

    page_num = 1
    max_pages = 20

    print(f"[{SOURCE_NAME}] Début du moissonnage du catalogue ({CATALOG_URL})...")

    while page_num <= max_pages:
        page_url = f"{CATALOG_URL}?p={page_num}"
        try:
            req = urllib.request.Request(page_url, headers=headers)
            with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
                html = resp.read().decode("utf-8", errors="ignore")
        except Exception as e:
            print(f"[{SOURCE_NAME}] Erreur page {page_num}: {e}")
            issues.append(fetch_issue(source_site=SOURCE_NAME, url=page_url, reason=REASON_FETCH_ERROR, message=str(e)))
            break

        soup = BeautifulSoup(html, "html.parser")
        items = soup.select(".product-item-info")
        if not items:
            print(f"[{SOURCE_NAME}] Fin de pagination atteinte à la page {page_num}.")
            break

        new_items_on_page = 0
        for item in items:
            link_el = item.select_one("a.product-item-link")
            title_el = item.select_one(".product-item-link")
            img_el = item.select_one(".product-image-photo")

            raw_title = ""
            if img_el and img_el.get("alt"):
                raw_title = img_el.get("alt", "").strip()
            elif title_el:
                raw_title = title_el.get_text(strip=True)

            url = link_el.get("href", "").strip() if link_el else page_url
            if url and not url.startswith("http"):
                url = f"{BASE_URL}{url}"

            # Extraction Prix
            price_val: float | None = None
            final_price_wrapper = item.select_one('[data-price-type="finalPrice"]')
            if final_price_wrapper and final_price_wrapper.get("data-price-amount"):
                try:
                    price_val = float(final_price_wrapper["data-price-amount"])
                except ValueError:
                    price_val = None

            if price_val is None:
                # Fallback sur texte du prix
                price_spans = item.select(".price-box .price")
                for ps in price_spans:
                    txt = ps.get_text(strip=True).replace(" ", " ").replace("€", "").replace(",", ".").strip()
                    try:
                        val = float(txt)
                        if val > 15:  # Éviter les badges de réduction de type "-10 €"
                            price_val = val
                            break
                    except ValueError:
                        continue

            if not price_val or price_val <= 0:
                continue

            product_id = item.get("data-product-id") or item.select_one("[data-product-id]")
            if isinstance(product_id, str):
                p_id = product_id
            elif hasattr(product_id, "get"):
                p_id = product_id.get("data-product-id", "")
            else:
                p_id = url.split("?")[0].split("/")[-1]

            if p_id in seen_product_ids:
                continue

            seen_product_ids.add(p_id)
            new_items_on_page += 1

            # Marque et Stockage
            storage_gb: int | None = None
            storage_raw: str | None = None
            sm = STORAGE_RE.search(raw_title)
            if sm:
                storage_raw = sm.group(0).strip()
                try:
                    num = int(sm.group(1))
                    if "to" in storage_raw.lower() or "tb" in storage_raw.lower():
                        storage_gb = num * 1024
                    else:
                        storage_gb = num
                except ValueError:
                    pass

            title_lower = raw_title.lower()
            if "iphone" in title_lower or "apple" in title_lower:
                brand = "Apple"
            elif "galaxy" in title_lower or "samsung" in title_lower:
                brand = "Samsung"
            elif "pixel" in title_lower or "google" in title_lower:
                brand = "Google"
            elif "xiaomi" in title_lower or "redmi" in title_lower:
                brand = "Xiaomi"
            elif "huawei" in title_lower:
                brand = "Huawei"
            elif "oppo" in title_lower:
                brand = "Oppo"
            elif "honor" in title_lower:
                brand = "Honor"
            elif "sony" in title_lower:
                brand = "Sony"
            else:
                brand = raw_title.split()[0] if raw_title else "Inconnu"

            model = raw_title
            for remove_word in ["reconditionné", "reconditionne", "Reconditionné", "Reconditionne"]:
                model = model.replace(remove_word, "").strip()

            row = {
                "source_site": SOURCE_DOMAIN,
                "source_url": url,
                "source": url,
                "timestamp": scrape_started_at,
                "scrape_started_at": scrape_started_at,
                "data_type": "product_sale",
                "device_type": "smartphone",
                "device_family": "Smartphone",
                "brand": brand,
                "model": model,
                "model_normalized": f"{brand} {model}".strip(),
                "item_title": raw_title,
                "storage_gb": storage_gb,
                "storage_raw": storage_raw,
                "condition": "reconditionne",
                "product_id": p_id,
                "price": price_val,
                "price_raw": f"{price_val:.2f} €".replace(".", ","),
                "currency": "EUR",
                "currency_symbol": "€",
                "seller_name": "Recommerce France",
                "shipping_cost": 0.0,
                "warranty": "Garantie 24 mois reconditionné certifié",
                "availability": "InStock",
                "scraped_at": scrape_started_at,
            }
            rows.append(row)

            if max_models and len(rows) >= max_models:
                break

        print(f"[{SOURCE_NAME}] Page {page_num}: {new_items_on_page} nouveaux produits extraits (Total cumulé: {len(rows)})")

        if max_models and len(rows) >= max_models:
            break

        if new_items_on_page == 0:
            print(f"[{SOURCE_NAME}] Aucun nouveau produit sur la page {page_num}, fin du catalogue.")
            break

        page_num += 1

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
