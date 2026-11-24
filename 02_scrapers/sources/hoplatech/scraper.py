"""Hoplatech Scraper (https://hoplatech.fr).

Atelier de réparation smartphones, tablettes, Mac et consoles à Strasbourg (Labellisé QualiRépar) :
- Tarifs par intervention et par modèle : Écran LCD, Écran OLED, Batterie, Connecteur de charge, Caméra, Lentille caméra, Face ID, Carte mère.
- Couverture : Apple (iPhone, iPad, Mac), Samsung Galaxy, Google Pixel, Xiaomi, consoles (Nintendo Switch, PS5, Xbox).

Stratégie d'extraction (Niveau 2 — Parsing Sitemap XML & Tableaux HTML / Pages Prestations Concurrent) :
    1. Récupération des URLs de modèles et prestations via le sitemap : /sitemap.xml (246 URLs)
    2. Extraction concurrente des pages avec ThreadPoolExecutor (15 workers)
    3. Parsing :
        - Gabarit 1 (Pages modèles avec tableaux) : table tr td[0] prestation, td[1] prix catalogue standard
        - Gabarit 2 (Pages prestations dédiées) : extraction du prix standard 'Avant bonus : XX €' / 'Prix net XX €'
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

BASE_URL = "https://hoplatech.fr"
SOURCE_NAME = "hoplatech"
SOURCE_DOMAIN = "hoplatech.fr"
SITEMAP_URL = f"{BASE_URL}/sitemap.xml"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)

SHOP_INFO = {
    "shop_name": "Hoplatech (Strasbourg - QualiRépar)",
    "address": "12 Rue du Faubourg-de-Pierre",
    "city": "Strasbourg",
    "postal_code": "67000",
    "delay": "Réparation express 30 minutes sans rendez-vous en atelier",
    "warranty": "Garantie pièces et main-d'œuvre (Labellisé QualiRépar)",
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


def extract_metadata_from_url(url: str, soup: BeautifulSoup) -> tuple[str, str, str]:
    rel = url.replace(BASE_URL, "").strip("/")
    parts = rel.split("/")
    brand = "Générique"
    model_name = "Modèle inconnu"
    family = "smartphone"

    if len(parts) >= 4 and parts[0] == "reparation":
        cat = parts[1]
        brand = parts[2].title()
        model_name = parts[3].replace("-", " ").title()

        if "tablette" in cat:
            family = "tablet"
        elif "console" in cat:
            family = "console"
        elif "mac" in cat or "ordinateur" in cat:
            family = "computer"
        elif "android" in cat:
            brand = "Samsung" if "galaxy" in parts[2].lower() else "Android"
            model_name = parts[2].replace("-", " ").title()

    elif len(parts) == 3 and parts[0] == "reparation":
        cat = parts[1]
        model_name = parts[2].replace("-", " ").title()
        if "console" in cat:
            family = "console"
            brand = "Nintendo" if "switch" in parts[2].lower() else ("Sony" if "ps" in parts[2].lower() else "Consoles")
        elif "mac" in cat:
            family = "computer"
            brand = "Apple"
        elif "tablette" in cat:
            family = "tablet"
            brand = "Apple"

    model_name = re.sub(r"\bIphone\b", "iPhone", model_name)
    model_name = re.sub(r"\bIpad\b", "iPad", model_name)

    h1 = soup.find("h1")
    if h1:
        h1_txt = h1.get_text(strip=True)
        m = re.search(r"Réparation\s+(?:.*?de charge|.*?batterie|.*?écran)?\s*(.*?)\s*à Strasbourg", h1_txt, flags=re.IGNORECASE)
        if m and len(m.group(1).strip()) > 2:
            extracted = m.group(1).strip()
            if not any(k in extracted.lower() for k in ["téléphone", "smartphone", "tablette"]):
                model_name = extracted

    return brand, model_name, family


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
            if loc.text and "/reparation" in loc.text:
                parts = loc.text.replace(BASE_URL, "").strip("/").split("/")
                if len(parts) >= 4 and parts[0] == "reparation":
                    model_urls.append(loc.text.strip())
                elif len(parts) == 3 and parts[1] in ("console", "mac", "tablettes"):
                    model_urls.append(loc.text.strip())

    model_urls = sorted(list(set(model_urls)))
    print(f"[{SOURCE_NAME.upper()}] {len(model_urls)} URLs de modèles trouvées dans le sitemap", flush=True)

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

    print(f"[{SOURCE_NAME.upper()}] {len(pages_html)} pages téléchargées. Étape 3 : Parsing des tableaux & prestations...", flush=True)

    rows: list[dict[str, Any]] = []
    for url, html in pages_html.items():
        soup = BeautifulSoup(html, "html.parser")
        brand, model_name, family = extract_metadata_from_url(url, soup)

        tables = soup.find_all("table")
        parsed_on_page = 0

        # Gabarit 1: Tableaux de réparations
        for table in tables:
            for tr in table.find_all("tr"):
                tds = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
                if len(tds) >= 2:
                    raw_label = tds[0]
                    if any(w in raw_label.lower() for w in ["réparation", "prestation", "panne", "intervention"]):
                        continue

                    price_str = tds[1]
                    price = parse_price(price_str)

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
                    parsed_on_page += 1

        # Gabarit 2: Fiche prestation unitaire sans table
        if parsed_on_page == 0:
            txt = soup.get_text(" ", strip=True)
            m_avant = re.search(r"Avant bonus\s*:\s*(\d+[\.,]?\d*)\s*€", txt)
            m_net = re.search(r"Prix net\s*(\d+[\.,]?\d*)\s*€", txt)
            price = None
            if m_avant:
                price = float(m_avant.group(1).replace(",", "."))
            elif m_net:
                price = float(m_net.group(1).replace(",", "."))

            if price and price > 0:
                # Infer prestation from URL
                parts = url.replace(BASE_URL, "").strip("/").split("/")
                slug_last = parts[-1]
                raw_label = slug_last.replace("-", " ").title()
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
