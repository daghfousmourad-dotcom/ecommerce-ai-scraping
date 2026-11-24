"""iServices Scraper (https://iservices.fr).

Réseau de magasins de réparation (Marseille, Toulon, Montpellier et national) :
- Tarifs par intervention et par modèle : Écran, Batterie, Connecteur de charge, Caméras, Son, etc.
- Couverture : Apple (iPhone, iPad, Watch, Mac), Samsung (Galaxy S, A, Note, Z, Tab, Watch), Xiaomi, Huawei, Oppo, OnePlus, Google Pixel.

Stratégie d'extraction (Niveau 2 — Parsing Sitemap XML & HTML Concurrent) :
    1. Récupération des URLs d'équipements via le sitemap : /feed/sitemap/sitemap-equipments.xml (963 URLs)
    2. Extraction concurrente des pages avec ThreadPoolExecutor
    3. Parsing SSR :
        - Fil d'Ariane & H1 pour Brand, Family, Model
        - Cartes d'interventions .product-box-repair a.box
        - Titre (.box-content-title) et Prix (.repair-regular-price ou .repair-price)
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

BASE_URL = "https://iservices.fr"
SOURCE_NAME = "iservices"
SOURCE_DOMAIN = "iservices.fr"
SITEMAP_URL = f"{BASE_URL}/feed/sitemap/sitemap-equipments.xml"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)

SHOP_INFO = {
    "shop_name": "iServices (France)",
    "address": "Magasins Marseille (Terrasses du Port & Prado), Toulon, Montpellier & Réseau national",
    "city": "Marseille",
    "postal_code": "13002",
    "delay": "20 à 30 minutes sans rendez-vous en atelier",
    "warranty": "Garantie pièces et main-d'œuvre",
}


def parse_price(val_str: str) -> float | None:
    cleaned = val_str.replace(" ", "").replace("\xa0", "").replace("&nbsp;", "")
    m = re.search(r"(\d+[\.,]?\d*)", cleaned)
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
            return url, resp.read().decode("utf-8", errors="replace"), None
    except Exception as e:
        return url, None, str(e)


def extract_model_and_brand(url: str, soup: BeautifulSoup) -> tuple[str, str, str]:
    # Try breadcrumbs first
    bc = [a.get_text(strip=True) for a in soup.select("nav ol li, .breadcrumb li, ul.breadcrumbs li") if a.get_text(strip=True)]
    # Typical breadcrumb: ['Accueil', 'Réparation', 'Apple', 'iPhone', 'iPhone X', 'iPhone X']
    # or ['Accueil', 'Réparation', 'Samsung', 'Galaxy S', 'Galaxy S24']
    brand = ""
    model_name = ""
    family = "smartphone"

    if len(bc) >= 4:
        brand = bc[2].strip()
        model_name = bc[-1].strip()
        cat = bc[3].lower() if len(bc) > 3 else ""
        if any(w in cat for w in ["watch", "montre"]):
            family = "smartwatch"
        elif any(w in cat for w in ["tab", "ipad", "tablette"]):
            family = "tablet"
        elif any(w in cat for w in ["mac", "pc", "ordinateur", "laptop"]):
            family = "computer"
        elif any(w in cat for w in ["console", "switch", "ps4", "ps5", "xbox"]):
            family = "console"

    if not brand or not model_name:
        # Fallback to H1
        h1 = soup.find("h1")
        h1_text = h1.get_text(strip=True) if h1 else ""
        h1_text = re.sub(r"^Réparation\s*", "", h1_text, flags=re.IGNORECASE)
        # Check known brands
        known_brands = ["Apple", "Samsung", "Xiaomi", "Huawei", "Oppo", "OnePlus", "Google", "Honor", "Sony"]
        for b in known_brands:
            if h1_text.lower().startswith(b.lower()):
                brand = b
                model_name = h1_text[len(b):].strip()
                break
        if not model_name:
            model_name = h1_text

    # URL path fallback
    if not brand or not model_name:
        parts = [p for p in url.replace(BASE_URL, "").strip("/").split("/") if p]
        # /reparation/apple/iphone/iphone-x/iphone-x
        if len(parts) >= 2:
            brand = parts[1].capitalize() if not brand else brand
        if len(parts) >= 4:
            model_name = parts[-1].replace("-", " ").title() if not model_name else model_name

    # Determine device family from model_name if needed
    lower_m = model_name.lower()
    if any(w in lower_m for w in ["watch", "montre"]):
        family = "smartwatch"
    elif any(w in lower_m for w in ["ipad", "tab", "tablette"]):
        family = "tablet"
    elif any(w in lower_m for w in ["macbook", "imac", "ordinateur"]):
        family = "computer"

    return brand or "Générique", model_name or "Inconnu", family


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

    print(f"[{SOURCE_NAME.upper()}] Étape 1 : Récupération du sitemap {SITEMAP_URL}...", flush=True)
    req = urllib.request.Request(SITEMAP_URL, headers=headers)
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
            xml_data = resp.read().decode("utf-8")
    except Exception as e:
        print(f"[{SOURCE_NAME.upper()}] Erreur sitemap: {e}", flush=True)
        xml_data = ""

    model_urls: list[str] = []
    if xml_data:
        root = ET.fromstring(xml_data)
        for loc in root.findall(".//{http://www.sitemaps.org/schemas/sitemap/0.9}loc"):
            if loc.text and "/reparation/" in loc.text:
                model_urls.append(loc.text.strip())

    model_urls = sorted(list(set(model_urls)))
    print(f"[{SOURCE_NAME.upper()}] {len(model_urls)} URLs d'équipements trouvées", flush=True)

    if max_models:
        model_urls = model_urls[:max_models]
        print(f"[{SOURCE_NAME.upper()}] Limitation à {len(model_urls)} URLs pour test", flush=True)

    print(f"[{SOURCE_NAME.upper()}] Étape 2 : Téléchargement concurrent des {len(model_urls)} pages (15 workers)...", flush=True)
    pages_html: dict[str, str] = {}
    issues: list[dict[str, Any]] = []

    with ThreadPoolExecutor(max_workers=15) as executor:
        futures = {executor.submit(fetch_url, url, ctx, headers): url for url in model_urls}
        for future in as_completed(futures):
            url, html, error = future.result()
            if error:
                issues.append(fetch_issue(source_site=SOURCE_NAME, url=url, reason=REASON_FETCH_ERROR, message=error))
            elif html:
                pages_html[url] = html

    print(f"[{SOURCE_NAME.upper()}] {len(pages_html)} pages téléchargées avec succès. Étape 3 : Parsing des tarifs...", flush=True)

    rows: list[dict[str, Any]] = []
    for url, html in pages_html.items():
        soup = BeautifulSoup(html, "html.parser")
        brand, model_name, family = extract_model_and_brand(url, soup)

        # Les cartes de réparation sont dans .product-box-repair a.box
        cards = soup.select(".product-box-repair a.box")
        for card in cards:
            title_el = card.select_one(".box-content-title")
            raw_label = title_el.get_text(strip=True) if title_el else ""
            if not raw_label:
                continue

            # Priorité au prix catalogue standard .repair-regular-price, fallback .repair-price
            reg_price_el = card.select_one(".repair-regular-price")
            rep_price_el = card.select_one(".repair-price")

            price = None
            price_raw_str = ""
            if reg_price_el:
                price_raw_str = reg_price_el.get_text(strip=True)
                price = parse_price(price_raw_str)
            if price is None and rep_price_el:
                price_raw_str = rep_price_el.get_text(strip=True)
                price = parse_price(price_raw_str)

            if price is None or price <= 0:
                continue

            norm_repair = normalize_repair_type(raw_label)

            row = {
                "source_site": SOURCE_DOMAIN,
                "source_url": url,
                "source": url,
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
                "repair_category": "Réparation en atelier",
                "scraped_at": scrape_started_at,
            }
            rows.append(row)

    initial_count = len(rows)
    rows = dedupe_dict_rows(rows, keys=("source_site", "brand", "model", "repair_type", "price"))
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
