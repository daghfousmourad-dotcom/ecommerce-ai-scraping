"""Fixmobile France Scraper (https://fixmobile.fr).

Atelier de réparation smartphone, tablette et informatique à Montigny-le-Bretonneux (SQY / Yvelines) :
- Tarifs par intervention et par modèle : Écran (OEM, Soft OLED, LCD Incell, Service Pack), Batterie, Connecteur, Vitre arrière, Caméra, Microsoudure.
- Couverture : Apple iPhone, Samsung Galaxy, Apple iPad, Google Pixel.

Stratégie d'extraction (Niveau 2 — Parsing Sitemap XML & HTML Concurrent) :
    1. Récupération des URLs de modèles via le sitemap des pages : /page-sitemap.xml
    2. Extraction concurrente des pages avec ThreadPoolExecutor
    3. Parsing multi-gabarits :
        - Gabarit 1 (Tables HTML) : pour les modèles historiques (Galaxy S/A, iPad)
        - Gabarit 2 (Cartes Elementor .fm-pricebox) : pour les modèles récents (iPhone 13 à 16 Pro Max)
        - Gabarit 3 (Conteneurs Elementor) : pour les grilles hybrides avec blocs prestations
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

BASE_URL = "https://fixmobile.fr"
SOURCE_NAME = "fixmobile"
SOURCE_DOMAIN = "fixmobile.fr"
SITEMAP_URL = f"{BASE_URL}/page-sitemap.xml"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)

SHOP_INFO = {
    "shop_name": "Fixmobile France (Montigny-le-Bretonneux)",
    "address": "Atelier Montigny-le-Bretonneux & Saint-Quentin-en-Yvelines",
    "city": "Montigny-le-Bretonneux",
    "postal_code": "78180",
    "delay": "20 à 30 minutes sans rendez-vous en atelier",
    "warranty": "Garantie 1 an pièces et main-d'œuvre",
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


def extract_model_and_brand(url: str, soup: BeautifulSoup) -> tuple[str, str, str]:
    """Returns (brand, model, device_family)."""
    title_str = soup.title.string.strip() if soup.title and soup.title.string else ""
    h1_el = soup.find("h1")
    h1_str = h1_el.get_text(" ", strip=True) if h1_el else ""
    slug = url.strip("/").split("/")[-1]

    # Brand
    brand = "Apple"
    family = "Smartphone"

    if "ipad" in slug or "ipad" in title_str.lower() or "ipad" in h1_str.lower():
        family = "Tablette"
        brand = "Apple"
    elif "samsung" in slug or "galaxy" in slug or "samsung" in title_str.lower() or "galaxy" in title_str.lower():
        brand = "Samsung"
        family = "Smartphone"
    elif "pixel" in slug or "pixel" in title_str.lower():
        brand = "Google"
        family = "Smartphone"
    elif "iphone" in slug or "iphone" in title_str.lower() or "iphone" in h1_str.lower():
        brand = "Apple"
        family = "Smartphone"

    # Model
    model = ""
    # Try H1 first
    if h1_str:
        m = re.search(r"(?:Réparation|Tarifs|Prix)\s+(.*?)(?:\s*:|\s*—|\s*\||$)", h1_str, flags=re.I)
        if m:
            cand = m.group(1).strip()
            if cand and len(cand) > 2 and not any(w in cand.lower() for w in ["smartphone", "express", "devis"]):
                model = cand

    # Try Title
    if not model and title_str:
        if "|" in title_str:
            cand = title_str.split("|")[0].strip()
            if cand and not any(w in cand.lower() for w in ["accueil", "réparation"]):
                model = cand
        elif "—" in title_str:
            cand = title_str.split("—")[0].strip()
            model = cand

    # Fallback from slug
    if not model:
        clean_slug = re.sub(r"^(?:reparations-tarifs-|reparation-)", "", slug)
        clean_slug = re.sub(r"-(?:ecran|batterie|carte-mere|microsoudure|prix|prestations).*$", "", clean_slug)
        clean_slug = clean_slug.replace("-", " ").strip()
        model = clean_slug.title()

    # Prepend brand if missing
    if brand == "Apple":
        if "ipad" in model.lower():
            full_model = model
        elif not model.lower().startswith("iphone"):
            full_model = f"iPhone {model}".strip()
        else:
            full_model = model
    elif brand == "Samsung":
        if not model.lower().startswith("galaxy") and not model.lower().startswith("samsung"):
            full_model = f"Galaxy {model}".strip()
        else:
            full_model = model
    elif brand == "Google":
        if not model.lower().startswith("pixel") and not model.lower().startswith("google"):
            full_model = f"Pixel {model}".strip()
        else:
            full_model = model
    else:
        full_model = model

    return brand, full_model, family


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
    model_urls: list[str] = []
    try:
        req = urllib.request.Request(SITEMAP_URL, headers=headers)
        with urllib.request.urlopen(req, context=ctx, timeout=12) as resp:
            root = ET.fromstring(resp.read())
            for elem in root.iter():
                if elem.tag.endswith("loc") and elem.text and elem.text.startswith("http"):
                    u = elem.text.strip()
                    if ("reparations-tarifs-" in u or ("reparation-" in u and any(m in u for m in ["iphone", "ipad", "samsung", "galaxy", "pixel"]))) and not any(ext in u for ext in [".png", ".jpg", "macbook", "switch", "ps5", "xbox", "pc-portable"]):
                        model_urls.append(u)
    except Exception as e:
        print(f"[{SOURCE_NAME}] Erreur sitemap: {e}")
        issues.append(fetch_issue(url=SITEMAP_URL, reason=REASON_FETCH_ERROR, message=str(e)))

    print(f"[{SOURCE_NAME}] Trouvé {len(model_urls)} pages de modèles éligibles dans le sitemap.")

    if max_models:
        model_urls = model_urls[:max_models]

    print(f"[{SOURCE_NAME}] Scraping concurrent de {len(model_urls)} pages...")
    pages_html: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(fetch_url, url, ctx, headers): url for url in model_urls}
        for future in as_completed(futures):
            url, html, error = future.result()
            if error:
                issues.append(fetch_issue(url=url, reason=REASON_FETCH_ERROR, message=error))
            elif html:
                pages_html[url] = html

    for url, html in pages_html.items():
        soup = BeautifulSoup(html, "html.parser")
        brand, model_name, family = extract_model_and_brand(url, soup)

        page_tariffs: list[tuple[str, float]] = []

        # 1. Parsing Tableaux HTML
        for t in soup.find_all("table"):
            for tr in t.find_all("tr"):
                tds = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
                if len(tds) >= 2:
                    label = tds[0]
                    p_str = tds[1]
                    m = re.search(r"(\d+[\.,]?\d*)\s*€", p_str)
                    if m and not any(w in label.lower() for w in ["cookie", "durée", "description", "prestation"]):
                        price = float(m.group(1).replace(",", "."))
                        if price > 0:
                            page_tariffs.append((label, price))

        # 2. Parsing Cartes Elementor .fm-pricebox
        for box in soup.find_all(class_="fm-pricebox"):
            m = re.search(r"(\d+[\.,]?\d*)\s*€", box.get_text())
            if m:
                price = float(m.group(1).replace(",", "."))
                if price > 0:
                    card = box.find_parent(class_=lambda c: c and any("card" in x or "item" in x for x in c)) or box.parent
                    title_el = card.find(["h2", "h3", "h4", "strong", "b"])
                    label = title_el.get_text(strip=True) if title_el else card.get_text(" ", strip=True)[:50]
                    page_tariffs.append((label, price))

        # 3. Parsing Conteneurs Elementor Hybrides
        if not page_tariffs:
            for sec in soup.find_all(class_="elementor-container"):
                t = sec.get_text(" | ", strip=True)
                if "€" in t and len(t) < 300 and any(w in t.lower() for w in ["écran", "batterie", "connecteur", "vitre", "caméra", "carte mère", "diagnostic"]):
                    m = re.search(r"(\d+[\.,]?\d*)\s*€", t)
                    if m:
                        price = float(m.group(1).replace(",", "."))
                        if price > 0:
                            label = t.split("|")[0].strip()
                            page_tariffs.append((label, price))

        for raw_label, price in page_tariffs:
            clean_label = re.sub(r"^[✓•\-\s]+", "", raw_label).strip()
            norm_repair = normalize_repair_type(clean_label)

            row = {
                "source_site": SOURCE_DOMAIN,
                "source_url": url,
                "source": url,
                "timestamp": scrape_started_at,
                "data_type": "repair_price",
                "device_family": family,
                "brand": brand,
                "model": model_name,
                "item_name": f"{clean_label} {brand} {model_name}",
                "repair_type_raw": clean_label,
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
