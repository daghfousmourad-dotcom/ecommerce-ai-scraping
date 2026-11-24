"""inforMathic.rep Scraper (https://www.informathic.fr).

Atelier de réparation informatique, smartphones, tablettes et consoles (Fontaine-le-Comte & national par envoi) :
- Tarifs par intervention et par modèle : Écran, Batterie, Connecteur de charge, Caméra, Désoxydation, Logiciel, Diagnostic.
- Couverture : Apple, Samsung, Huawei, Xiaomi, Wiko, Sony, Oppo, Redmi, Honor, tablettes (iPad, Galaxy Tab), ordinateurs, consoles (PS4, PS5, Xbox, Switch).

Stratégie d'extraction (Niveau 2 — Crawl hiérarchique PHP & Parsing HTML Concurrent) :
    1. Découverte des catégories : telephone, tablette, ordinateur, console, autre
    2. Découverte des marques par catégorie (site.php?p=tarifs&categorie=...)
    3. Découverte des modèles par marque (site.php?p=tarifs&categorie=...&marque=...)
    4. Extraction concurrente des pages modèles avec ThreadPoolExecutor
    5. Parsing des lignes de prix (.ligne-prix .lp-nom, .lp-prix)
    6. Normalisation standard 22 colonnes E-commerce AI V2.
    7. Exportation certifiée des 5 artefacts standard (.jsonl, .csv, .json, _run.json, _issues.jsonl).
"""

from __future__ import annotations

import argparse
import html as html_lib
import json
import re
import ssl
import sys
import urllib.parse
import urllib.request
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

BASE_URL = "https://www.informathic.fr"
SOURCE_NAME = "informathic"
SOURCE_DOMAIN = "informathic.fr"
TARIFS_URL = f"{BASE_URL}/site.php?p=tarifs"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)

SHOP_INFO = {
    "shop_name": "inforMathic.rep (Atelier & Envois postaux)",
    "address": "14 Rue de la Fontaine",
    "city": "Fontaine-le-Comte",
    "postal_code": "86240",
    "delay": "24h à 48h en atelier ou retour colis national",
    "warranty": "Garantie pièces et main-d'œuvre",
}

CATEGORY_MAP = {
    "telephone": "smartphone",
    "tablette": "tablet",
    "ordinateur": "computer",
    "console": "console",
    "autre": "other",
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

    print(f"[{SOURCE_NAME.upper()}] Étape 1 : Exploration de l'arborescence des tarifs...", flush=True)

    # 1. Catégories
    categories = ["telephone", "tablette", "ordinateur", "console", "autre"]
    brand_urls: list[tuple[str, str]] = []  # (category, url)

    for cat in categories:
        cat_url = f"{BASE_URL}/site.php?p=tarifs&categorie={cat}"
        _, html, err = fetch_url(cat_url, ctx, headers)
        if not html:
            continue
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a"):
            href = a.get("href", "")
            if href and f"categorie={cat}" in href and "&marque=" in href:
                full_url = urllib.parse.urljoin(BASE_URL, href)
                brand_urls.append((cat, full_url))

    print(f"[{SOURCE_NAME.upper()}] {len(brand_urls)} couples catégorie-marque identifiés", flush=True)

    # 2. Modèles par marque
    model_items: list[dict[str, str]] = []  # dict with url, cat, marque, modele
    seen_model_urls: set[str] = set()

    for cat, b_url in brand_urls:
        _, html, err = fetch_url(b_url, ctx, headers)
        if not html:
            continue
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a"):
            href = a.get("href", "")
            if href and "&modele=" in href:
                full_url = urllib.parse.urljoin(BASE_URL, href)
                if full_url not in seen_model_urls:
                    seen_model_urls.add(full_url)
                    # parse query params
                    parsed = urllib.parse.urlparse(full_url)
                    qs = urllib.parse.parse_qs(parsed.query)
                    marque = qs.get("marque", [""])[0]
                    modele = qs.get("modele", [""])[0]
                    model_items.append({
                        "url": full_url,
                        "category": cat,
                        "brand": marque,
                        "model": modele,
                    })

    print(f"[{SOURCE_NAME.upper()}] {len(model_items)} modèles identifiés dans le catalogue", flush=True)

    if max_models:
        model_items = model_items[:max_models]
        print(f"[{SOURCE_NAME.upper()}] Limitation à {len(model_items)} modèles pour test", flush=True)

    # 3. Fetching concurrent des fiches modèles
    print(f"[{SOURCE_NAME.upper()}] Étape 2 : Téléchargement concurrent des {len(model_items)} pages modèles (15 workers)...", flush=True)
    pages_html: dict[str, str] = {}
    issues: list[dict[str, Any]] = []

    with ThreadPoolExecutor(max_workers=15) as executor:
        futures = {executor.submit(fetch_url, item["url"], ctx, headers): item for item in model_items}
        for future in as_completed(futures):
            item = futures[future]
            url, html, error = future.result()
            if error:
                issues.append(fetch_issue(source_site=SOURCE_NAME, url=url, reason=REASON_FETCH_ERROR, message=error))
            elif html:
                pages_html[url] = html

    print(f"[{SOURCE_NAME.upper()}] {len(pages_html)} pages téléchargées. Étape 3 : Parsing des tarifs...", flush=True)

    item_lookup = {item["url"]: item for item in model_items}
    rows: list[dict[str, Any]] = []

    for url, html in pages_html.items():
        info = item_lookup.get(url, {})
        cat = info.get("category", "telephone")
        brand = info.get("brand", "Générique")
        model_name = info.get("model", "Inconnu")
        family = CATEGORY_MAP.get(cat, "smartphone")

        soup = BeautifulSoup(html, "html.parser")
        lines = soup.select(".ligne-prix")

        for line in lines:
            nom_el = line.select_one(".lp-nom")
            prix_el = line.select_one(".lp-prix")
            if not nom_el or not prix_el:
                continue

            raw_label = nom_el.get_text(strip=True)
            price_str = prix_el.get_text(strip=True)
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
                "repair_category": "Réparation en atelier / à distance",
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
