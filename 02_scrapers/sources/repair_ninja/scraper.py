"""Repair Ninja Scraper (https://repairninja.fr).

Atelier de réparation smartphones à Paris Bastille (11e arrondissement) :
- Tarifs par intervention et par modèle : Écran, Batterie, Vitre arrière.
- Couverture : Apple iPhone, Samsung Galaxy séries A, S, Z Flip.

Stratégie d'extraction (Niveau 2 — Parsing Sitemap XML & HTML Concurrent) :
    1. Récupération des URLs de réparation dédiées via le sitemap officiel : /sitemap.xml
    2. Extraction concurrente des pages avec ThreadPoolExecutor
    3. Parsing des tarifs, modèles, garanties et délais dans les balises structurées .rp-price / .rp-trust
    4. Normalisation standard 22 colonnes E-commerce AI V2.
    5. Exportation certifiée des 5 artefacts standard (.jsonl, .csv, .json, _run.json, _issues.jsonl).
"""

from __future__ import annotations

import argparse
import json
import re
import ssl
import sys
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
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

BASE_URL = "https://repairninja.fr"
SOURCE_NAME = "repair_ninja"
SOURCE_DOMAIN = "repairninja.fr"
SITEMAP_URL = f"{BASE_URL}/sitemap.xml"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)

SHOP_INFO = {
    "shop_name": "Repair Ninja Paris Bastille",
    "address": "Atelier Paris Bastille",
    "city": "Paris",
    "postal_code": "75011",
    "delay": "30 min sur place",
    "warranty": "Garantie 6 à 12 mois (compatible 6 mois, origine 12 mois)",
}


def parse_price(val_str: str) -> float | None:
    m = re.search(r"(\d+[\.,]?\d*)", val_str.replace(" ", "").replace("\xa0", ""))
    if m:
        try:
            return float(m.group(1).replace(",", "."))
        except ValueError:
            return None
    return None


def fetch_url(url: str, ctx: ssl.SSLContext, headers: dict[str, str]) -> tuple[str, str | None, str | None]:
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=12) as resp:
            return url, resp.read().decode("utf-8"), None
    except Exception as e:
        return url, None, str(e)


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
    prefix = f"{SOURCE_NAME}_repair_prices_{timestamp_clean}"
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

    print(f"[{SOURCE_NAME}] Téléchargement du sitemap ({SITEMAP_URL})...")
    repair_urls: list[str] = []
    try:
        req = urllib.request.Request(SITEMAP_URL, headers=headers)
        with urllib.request.urlopen(req, context=ctx, timeout=12) as resp:
            root = ET.fromstring(resp.read())
            for elem in root.iter():
                if elem.tag.endswith("loc") and elem.text and "/reparation/" in elem.text:
                    repair_urls.append(elem.text.strip())
    except Exception as e:
        print(f"[{SOURCE_NAME}] Erreur sitemap: {e}")
        issues.append(fetch_issue(url=SITEMAP_URL, reason=REASON_FETCH_ERROR, message=str(e)))

    print(f"[{SOURCE_NAME}] Trouvé {len(repair_urls)} pages de réparations dans le sitemap.")

    if max_models:
        repair_urls = repair_urls[:max_models]

    # Téléchargement concurrent
    print(f"[{SOURCE_NAME}] Scraping concurrent de {len(repair_urls)} pages...")
    pages_html: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(fetch_url, url, ctx, headers): url for url in repair_urls}
        for future in as_completed(futures):
            url, html, error = future.result()
            if error:
                issues.append(fetch_issue(url=url, reason=REASON_FETCH_ERROR, message=error))
            elif html:
                pages_html[url] = html

    for url, html in pages_html.items():
        soup = BeautifulSoup(html, "html.parser")
        title_str = soup.title.string.strip() if soup.title and soup.title.string else ""
        h1_el = soup.find("h1")
        h1_str = h1_el.get_text(" ", strip=True) if h1_el else ""

        # Price extraction
        price: float | None = None
        price_box = soup.find(class_="rp-price")
        if price_box:
            m = re.search(r"(\d+[\.,]?\d*)\s*€", price_box.get_text())
            if m:
                price = parse_price(m.group(1))

        if not price and title_str:
            m = re.search(r"(\d+[\.,]?\d*)\s*€", title_str)
            if m:
                price = parse_price(m.group(1))

        if not price or price <= 0:
            continue

        price_raw_str = f"{int(price) if price.is_integer() else price} €"

        # Determine repair type
        slug = url.split("/reparation/")[-1]
        raw_repair = "Réparation"
        if "ecran" in slug or "écran" in title_str.lower() or "écran" in h1_str.lower():
            raw_repair = "Remplacement Écran"
        elif "batterie" in slug or "batterie" in title_str.lower() or "batterie" in h1_str.lower():
            raw_repair = "Remplacement Batterie"
        elif "vitre-arriere" in slug or "vitre arrière" in title_str.lower() or "vitre arrière" in h1_str.lower():
            raw_repair = "Remplacement Vitre arrière"
        elif "connecteur" in slug or "connecteur" in title_str.lower():
            raw_repair = "Réparation Connecteur de charge"

        norm_repair = normalize_repair_type(raw_repair)

        # Determine brand and model
        brand = "Apple"
        if "samsung" in slug or "galaxy" in slug or "samsung" in title_str.lower() or "galaxy" in title_str.lower():
            brand = "Samsung"

        # Parse model names from title or slug
        # ex title: "Réparation écran iPhone 8 / SE 2 / SE 3 à Paris Bastille — 49€ | Repair Ninja"
        model_part = ""
        if " — " in title_str:
            before_dash = title_str.split(" — ")[0]
            # remove "Réparation écran / batterie ... à Paris Bastille"
            before_dash = re.sub(r"^Réparation\s+(?:écran|batterie|vitre arrière)\s+", "", before_dash, flags=re.I)
            before_dash = re.sub(r"\s+à\s+Paris.*$", "", before_dash, flags=re.I).strip()
            model_part = before_dash

        if not model_part:
            # Fallback on slug
            clean_slug = re.sub(r"^(?:ecran|batterie|vitre-arriere)-", "", slug)
            clean_slug = clean_slug.replace("-", " ")
            model_part = clean_slug.title()

        # Expand multiple models if grouped: "iPhone 8 / SE 2 / SE 3"
        parts = [p.strip() for p in model_part.split("/") if p.strip()]
        for p in parts:
            if not p:
                continue
            if brand == "Apple" and not p.lower().startswith("iphone"):
                full_model = f"iPhone {p}"
            elif brand == "Samsung" and not p.lower().startswith("galaxy"):
                full_model = f"Galaxy {p}"
            else:
                full_model = p

            row = {
                "source_site": SOURCE_DOMAIN,
                "source_url": url,
                "source": url,
                "timestamp": scrape_started_at,
                "data_type": "repair_price",
                "device_family": "Smartphone",
                "brand": brand,
                "model": full_model,
                "item_name": f"{raw_repair} {brand} {full_model}",
                "repair_type_raw": raw_repair,
                "repair_type": norm_repair,
                "price": price,
                "price_raw": price_raw_str,
                "currency": "EUR",
                "currency_symbol": "€",
                "shop_name": SHOP_INFO["shop_name"],
                "address": SHOP_INFO["address"],
                "city": SHOP_INFO["city"],
                "postal_code": SHOP_INFO["postal_code"],
                "delay": SHOP_INFO["delay"],
                "warranty": SHOP_INFO["warranty"],
                "model_sku": full_model.lower().replace(" ", "_"),
                "repair_category": "Réparation en atelier",
                "scraped_at": scrape_started_at,
            }
            rows.append(row)

    initial_count = len(rows)
    rows = dedupe_dict_rows(rows, keys=("source_site", "brand", "model", "repair_type", "price"))
    print(f"[{SOURCE_NAME}] Total lignes brutes: {initial_count}, après déduplication: {len(rows)}")

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
        if clean_report:
            print(clean_report.summary_line(), flush=True)

    for row in rows:
        row.setdefault("scrape_started_at", scrape_started_at)

    # Export des 5 artefacts
    write_jsonl_and_csv(rows, jsonl_path=jsonl_path, csv_path=csv_path)

    payload = {
        "source_site": SOURCE_NAME,
        "scrape_started_at": scrape_started_at,
        "row_count": len(rows),
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
