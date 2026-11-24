"""SLM Mobiles Scraper (https://slmmobiles.fr).

Atelier de réparation smartphones et multimédia à Vannes (labellisé QualiRépar) :
- Univers : repair_price (Tarifs de réparation smartphones toutes marques)
- Sitemap officiel : https://slmmobiles.fr/sitemap.xml (filtrage /reparation/smartphone/)
- Données : prestations multi-gammes (Écran ÉCO, CLASSIC, ORIGINE, Batterie, Connecteur, Caméra, etc.)
  avec prix brut avant bonus et prix net déduit du bonus QualiRépar (15€ à 25€).
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

SOURCE_NAME = "slmmobiles"
SOURCE_DOMAIN = "slmmobiles.fr"
SITEMAP_URL = "https://slmmobiles.fr/sitemap.xml"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)

CANONICAL_REPAIR_MAP = {
    "écran": "Écran",
    "ecran": "Écran",
    "batterie": "Batterie",
    "connecteur": "Connecteur de charge",
    "haut-parleur": "Audio / Haut-parleur",
    "écouteur": "Écouteur interne",
    "micro": "Micro",
    "photo": "Caméra arrière",
    "lentille": "Lentille caméra",
    "bouton": "Boutons & Nappes",
    "power": "Boutons & Nappes",
    "volume": "Boutons & Nappes",
}


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


def map_repair_category(title: str) -> str:
    low = title.lower()
    for kw, cat in CANONICAL_REPAIR_MAP.items():
        if kw in low:
            return cat
    return "Autre réparation"


def parse_model_page(url: str, html: str, scraped_at: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    rows: list[dict[str, Any]] = []

    h1 = soup.select_one("h1")
    raw_h1 = h1.get_text(strip=True) if h1 else ""
    model = re.sub(r"^Réparation\s+", "", raw_h1, flags=re.I)
    model = re.sub(r"\s+à\s+Vannes\.?$", "", model, flags=re.I).strip()

    # Brand from URL path: /reparation/smartphone/<brand>/<model>/
    parts = url.replace("https://slmmobiles.fr/reparation/smartphone/", "").strip("/").split("/")
    brand_slug = parts[0] if parts else ""
    brand = brand_slug.capitalize()
    if brand.lower() == "apple":
        brand = "Apple"
    elif brand.lower() == "samsung":
        brand = "Samsung"
    elif brand.lower() == "xiaomi":
        brand = "Xiaomi"
    elif brand.lower() == "huawei":
        brand = "Huawei"
    elif brand.lower() == "google":
        brand = "Google"

    buttons = soup.select("button")
    for b in buttons:
        txt = b.get_text()
        if "€" not in txt:
            continue

        title_span = b.select_one("span.min-w-0 span.block.font-semibold")
        title = title_span.get_text(strip=True) if title_span else ""
        if not title:
            continue

        sub_span = b.select_one("span.min-w-0 span.text-xs")
        subtext = sub_span.get_text(strip=True) if sub_span else ""

        price_span = b.select_one("span.shrink-0 span.text-lg")
        price_txt = price_span.get_text(strip=True) if price_span else ""

        m_net = re.search(r"(\d+[\.,]?\d*)\s*€", price_txt)
        m_gross = re.search(r"(\d+[\.,]?\d*)\s*€\s*avant\s*bonus", subtext)

        net_val = float(m_net.group(1).replace(",", ".")) if m_net else None
        gross_val = float(m_gross.group(1).replace(",", ".")) if m_gross else net_val

        price_final = gross_val or net_val
        if not price_final or price_final <= 0:
            continue

        canonical_cat = map_repair_category(title)
        item_name = f"{title} {brand} {model}".strip()

        row = {
            "source_site": SOURCE_DOMAIN,
            "source_url": url,
            "source": url,
            "timestamp": scraped_at,
            "scrape_started_at": scraped_at,
            "data_type": "repair_price",
            "device_type": "smartphone",
            "device_family": "Smartphone",
            "brand": brand,
            "model": model or raw_h1,
            "model_normalized": f"{brand} {model}".strip() if model else raw_h1,
            "item_name": item_name,
            "repair_category": canonical_cat,
            "repair_type": canonical_cat,
            "repair_type_raw": title,
            "repair_description": subtext,
            "price": price_final,
            "price_raw": f"{price_final:.2f} €".replace(".", ","),
            "price_net": net_val,
            "currency": "EUR",
            "currency_symbol": "€",
            "workshop_name": "SLM Mobiles Vannes",
            "seller_name": "SLM Mobiles",
            "warranty": "Garantie 12 mois pièces et main-d'œuvre (Labellisé QualiRépar)",
            "scraped_at": scraped_at,
        }
        rows.append(row)

    return rows


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

    convenience_jsonl = out_dir / f"db_{SOURCE_NAME}.jsonl"
    convenience_csv = out_dir / f"db_{SOURCE_NAME}.csv"
    convenience_json = out_dir / f"db_{SOURCE_NAME}.json"

    issues: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []

    session = get_session()

    print(f"[{SOURCE_NAME}] Téléchargement du sitemap ({SITEMAP_URL})...")
    leaf_urls: list[str] = []
    try:
        resp = session.get(SITEMAP_URL, timeout=15)
        if resp.status_code == 200:
            root = ET.fromstring(resp.content)
            for elem in root.iter():
                if elem.tag.endswith("loc") and elem.text:
                    u = elem.text.strip()
                    if "/reparation/smartphone/" in u:
                        rel = u.replace("https://slmmobiles.fr/reparation/smartphone/", "").strip("/")
                        if rel.count("/") >= 1:
                            leaf_urls.append(u)
    except Exception as e:
        print(f"[{SOURCE_NAME}] Erreur sitemap: {e}")
        issues.append(fetch_issue(source_site=SOURCE_NAME, url=SITEMAP_URL, reason=REASON_FETCH_ERROR, message=str(e)))

    leaf_urls = list(dict.fromkeys(leaf_urls))
    print(f"[{SOURCE_NAME}] Trouvé {len(leaf_urls)} fiches modèles smartphones dans le sitemap.")

    if max_models:
        leaf_urls = leaf_urls[:max_models]

    print(f"[{SOURCE_NAME}] Extraction concurrente de {len(leaf_urls)} modèles...")
    pages_html: dict[str, str] = {}

    def fetch_url(u: str):
        try:
            r = session.get(u, timeout=12)
            if r.status_code == 200:
                return u, r.text, None
            return u, None, f"HTTP {r.status_code}"
        except Exception as e:
            return u, None, str(e)

    with ThreadPoolExecutor(max_workers=15) as executor:
        futures = {executor.submit(fetch_url, u): u for u in leaf_urls}
        for future in as_completed(futures):
            u, html, err = future.result()
            if err:
                issues.append(fetch_issue(source_site=SOURCE_NAME, url=u, reason=REASON_FETCH_ERROR, message=err))
            elif html:
                pages_html[u] = html

    for u, html in pages_html.items():
        parsed = parse_model_page(u, html, scrape_started_at)
        rows.extend(parsed)

    initial_count = len(rows)
    rows = dedupe_dict_rows(rows, keys=("source_site", "brand", "model", "repair_type_raw", "price"))
    print(f"[{SOURCE_NAME}] Total lignes brutes: {initial_count}, après déduplication: {len(rows)}")

    clean_report = None
    fetch_errors = len(issues)
    if apply_cleaning and rows:
        cleaning_cfg = CleaningConfig(
            price_min=10.0,
            price_max=3000.0,
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
