"""All4iphone Scraper (https://www.all4iphone.fr).

Atelier français de réparation et pièces détachées (Cergy) :
- Univers : repair_price (Forfaits de réparation smartphones et tablettes Apple/Samsung)
- Sitemap officiel : https://www.all4iphone.fr/1_fr_0_sitemap.xml (filtrage frep*.html)
- Données : prix fixes TTC par panne (Écran, Batterie, Connecteur, Tactile, Boutons) avec garantie.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
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

BASE_URL = "https://www.all4iphone.fr"
SOURCE_NAME = "all4iphone"
SOURCE_DOMAIN = "all4iphone.fr"
SITEMAP_URL = f"{BASE_URL}/1_fr_0_sitemap.xml"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)


def get_session() -> requests.Session:
    s = requests.Session()
    retries = Retry(total=3, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503, 504])
    s.mount("https://", HTTPAdapter(pool_connections=20, pool_maxsize=20, max_retries=retries))
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "fr-FR,fr;q=0.9",
    })
    return s


def fetch_url(url: str, session: requests.Session) -> tuple[str, str | None, str | None]:
    try:
        r = session.get(url, timeout=12)
        if r.status_code == 200:
            return url, r.text, None
        return url, None, f"HTTP {r.status_code}"
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

    issues: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []

    session = get_session()

    print(f"[{SOURCE_NAME}] Téléchargement du sitemap ({SITEMAP_URL})...")
    repair_urls: list[str] = []
    try:
        resp = session.get(SITEMAP_URL, timeout=15)
        if resp.status_code == 200:
            root = ET.fromstring(resp.content)
            for elem in root.iter():
                if elem.tag.endswith("loc") and elem.text:
                    url = elem.text.strip()
                    if "frep" in url.lower() and url.endswith(".html"):
                        repair_urls.append(url)
    except Exception as e:
        print(f"[{SOURCE_NAME}] Erreur sitemap: {e}")
        issues.append(fetch_issue(source_site=SOURCE_NAME, url=SITEMAP_URL, reason=REASON_FETCH_ERROR, message=str(e)))

    repair_urls = list(dict.fromkeys(repair_urls))
    print(f"[{SOURCE_NAME}] Trouvé {len(repair_urls)} forfaits de réparation (frep) dans le sitemap.")

    if max_models:
        repair_urls = repair_urls[:max_models]

    print(f"[{SOURCE_NAME}] Scraping concurrent de {len(repair_urls)} forfaits...")
    pages_html: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(fetch_url, url, session): url for url in repair_urls}
        for future in as_completed(futures):
            url, html, error = future.result()
            if error:
                issues.append(fetch_issue(source_site=SOURCE_NAME, url=url, reason=REASON_FETCH_ERROR, message=error))
            elif html:
                pages_html[url] = html

    for url, html in pages_html.items():
        soup = BeautifulSoup(html, "html.parser")

        # Titre de la prestation
        h1 = soup.select_one("h1[itemprop='name'], h1.h1, h1")
        raw_title = h1.get_text(strip=True) if h1 else ""

        # Prix
        price_val: float | None = None
        price_el = soup.select_one("[itemprop='price'], .current-price span[content], .product-price")
        if price_el:
            content_val = price_el.get("content")
            if content_val:
                try:
                    price_val = float(str(content_val).replace(",", "."))
                except ValueError:
                    price_val = None

        if price_val is None:
            price_spans = soup.select(".current-price, .product-price, .price")
            for ps in price_spans:
                txt = ps.get_text(strip=True).replace(" ", "").replace(" ", "").replace("€", "").replace(",", ".").strip()
                m = re.search(r"(\d+\.?\d*)", txt)
                if m:
                    try:
                        v = float(m.group(1))
                        if 10.0 <= v <= 2000.0:
                            price_val = v
                            break
                    except ValueError:
                        pass

        if not price_val or price_val <= 0 or not raw_title:
            continue

        # Détection marque et modèle
        title_lower = raw_title.lower()
        if "ipad" in title_lower or "tablette" in title_lower or "tab" in title_lower:
            device_type = "tablette"
            device_family = "Tablette"
        else:
            device_type = "smartphone"
            device_family = "Smartphone"

        if "iphone" in title_lower or "ipad" in title_lower or "apple" in title_lower:
            brand = "Apple"
        elif "galaxy" in title_lower or "samsung" in title_lower:
            brand = "Samsung"
        elif "huawei" in title_lower:
            brand = "Huawei"
        elif "xiaomi" in title_lower or "redmi" in title_lower:
            brand = "Xiaomi"
        else:
            brand = "Autre"

        # Catégorie de panne
        if any(k in title_lower for k in ["ecran", "écran", "vitre", "tactile", "lcd", "afficheur"]):
            repair_cat = "Écran"
        elif "batterie" in title_lower:
            repair_cat = "Batterie"
        elif any(k in title_lower for k in ["connecteur", "charge", "usb"]):
            repair_cat = "Connecteur de charge"
        elif any(k in title_lower for k in ["camera", "caméra", "lentille"]):
            repair_cat = "Caméra"
        elif any(k in title_lower for k in ["haut-parleur", "haut parleur", "ecouteur", "micro"]):
            repair_cat = "Audio / Haut-parleur"
        elif any(k in title_lower for k in ["bouton", "nappe"]):
            repair_cat = "Boutons & Nappes"
        elif any(k in title_lower for k in ["arrière", "arriere", "dos", "chassis"]):
            repair_cat = "Vitre arrière / Châssis"
        elif "desoxydation" in title_lower or "désoxydation" in title_lower:
            repair_cat = "Désoxydation"
        else:
            repair_cat = "Autre réparation"

        # Modèle
        model = raw_title
        for rem in [
            "Réparation", "Reparation", "Forfait", "remplacement", "Remplacement", 
            "Du", "du", "d'", "d’", "de", "la", "le", "l'", "l’", "des"
        ]:
            model = re.sub(rf"\b{rem}\b", "", model, flags=re.I).strip()
        for rem_cat in [
            "Écran", "Ecran", "Tactile", "Batterie", "Connecteur de charge", "Connecteur", 
            "Bouton home", "Bouton", "Haut-parleur", "Haut parleur", "Caméra", "Camera", 
            "Vitre arrière", "Vitre", "Châssis", "Chassis", "Micro", "Écouteur", "Ecouteur"
        ]:
            model = re.sub(rf"\b{rem_cat}\b", "", model, flags=re.I).strip()
        model = re.sub(r"\s+", " ", model).strip()
        if not model:
            model = raw_title

        row = {
            "source_site": SOURCE_DOMAIN,
            "source_url": url,
            "source": url,
            "timestamp": scrape_started_at,
            "scrape_started_at": scrape_started_at,
            "data_type": "repair_price",
            "device_type": device_type,
            "device_family": device_family,
            "brand": brand,
            "model": model or raw_title,
            "model_normalized": f"{brand} {model}".strip() if model else raw_title,
            "repair_category": repair_cat,
            "repair_type": raw_title,
            "repair_type_raw": raw_title,
            "price": price_val,
            "price_raw": f"{price_val:.2f} €".replace(".", ","),
            "currency": "EUR",
            "currency_symbol": "€",
            "workshop_name": "All4iphone France",
            "seller_name": "All4iphone France",
            "warranty": "Garantie 6 mois pièces et main d'œuvre",
            "scraped_at": scrape_started_at,
        }
        rows.append(row)

    initial_count = len(rows)
    rows = dedupe_dict_rows(rows, keys=("source_site", "brand", "model", "repair_type_raw", "price"))
    print(f"[{SOURCE_NAME}] Total lignes brutes: {initial_count}, après déduplication: {len(rows)}")

    clean_report = None
    fetch_errors = len(issues)
    if apply_cleaning and rows:
        cleaning_cfg = CleaningConfig(
            price_min=10.0,
            price_max=2500.0,
            dedupe_keys_repair_price=("source_url", "repair_type_raw", "price_raw"),
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

    write_jsonl_and_csv(rows, jsonl_path=jsonl_path, csv_path=csv_path)

    payload = {
        "source_site": SOURCE_NAME,
        "scrape_started_at": scrape_started_at,
        "row_count": len(rows),
        "rows": rows,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    try:
        import shutil
        shutil.copy(jsonl_path, convenience_jsonl)
        shutil.copy(csv_path, convenience_csv)
        shutil.copy(json_path, convenience_json)
    except Exception:
        pass

    print(f"[{SOURCE_NAME.upper()}] ✅ Fin d'exécution : {len(rows)} forfaits exportés dans {out_dir}", flush=True)

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
