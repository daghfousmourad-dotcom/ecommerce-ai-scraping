"""asgoodasnew Scraper (https://asgoodasnew.fr).

Plateforme européenne de smartphones, tablettes, MacBook et smartwatches reconditionnés :
- Univers : product_sales (Catalogue multimarque certifié 30 mois)
- Protection : Cloudflare / Smoxy Proof-of-Work Challenge WAF.
- Stratégie d'extraction : Stratégie 5 (WAF Resolution Playwright --headless=new) + Stratégie 2 (context.request & JSON-LD).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
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

BASE_URL = "https://asgoodasnew.fr"
SOURCE_NAME = "asgoodasnew"
SOURCE_DOMAIN = "asgoodasnew.fr"

CATEGORIES = [
    ("/smartphones/", "smartphone", "Smartphone", 10),
    ("/tablettes/", "tablette", "Tablette", 5),
    ("/ordinateurs-portables/", "ordinateur", "Ordinateur", 5),
    ("/montres-connectees/", "smartwatch", "Montre connectée", 5),
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
    headless: bool = True,
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

    issues: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []

    print(f"[{SOURCE_NAME}] Initialisation du moteur Playwright Chromium (résolution WAF Smoxy)...")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        err_msg = "Playwright n'est pas installé dans l'environnement Python"
        print(f"[{SOURCE_NAME}] ❌ {err_msg}")
        issues.append(fetch_issue(source_site=SOURCE_NAME, url=BASE_URL, reason=REASON_FETCH_ERROR, message=err_msg))
        return []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=[
                "--headless=new",
                "--disable-blink-features=AutomationControlled",
                "--enable-webgl",
                "--use-gl=angle",
                "--use-angle=metal",
            ],
        )
        context = browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1440, "height": 900},
            locale="fr-FR",
        )
        page = context.new_page()
        page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            window.navigator.chrome = { runtime: {} };
        """)

        # Étape 1 : Résolution active du challenge Smoxy
        initial_url = f"{BASE_URL}/smartphones/"
        print(f"[{SOURCE_NAME}] Négociation du jeton WAF Smoxy sur {initial_url}...")
        try:
            page.goto(initial_url, timeout=30000)
            bypassed = False
            for i in range(15):
                time.sleep(1)
                t = page.title()
                cookies = context.cookies()
                if any(c['name'] == 'sx_challenge' for c in cookies) and "smoxy" not in t.lower() and "loading" not in t.lower():
                    bypassed = True
                    print(f"[{SOURCE_NAME}] ✅ Challenge Smoxy résolu avec succès en {i+1}s !")
                    break

            if not bypassed:
                warn_msg = "Échec de résolution du challenge Smoxy après 15s"
                print(f"[{SOURCE_NAME}] ⚠️ {warn_msg}")
                issues.append(fetch_issue(source_site=SOURCE_NAME, url=initial_url, reason=REASON_FETCH_ERROR, message=warn_msg))
                browser.close()
                return []
        except Exception as e:
            print(f"[{SOURCE_NAME}] Erreur initiale WAF: {e}")
            issues.append(fetch_issue(source_site=SOURCE_NAME, url=initial_url, reason=REASON_FETCH_ERROR, message=str(e)))
            browser.close()
            return []

        # Étape 2 : Extraction concurrente des univers avec context.request
        seen_product_ids: set[str] = set()

        for cat_path, dev_type, dev_family, max_pages in CATEGORIES:
            print(f"[{SOURCE_NAME}] Exploration univers {cat_path} (jusqu'à {max_pages} pages)...")

            for page_idx in range(max_pages):
                page_url = f"{BASE_URL}{cat_path}?pgNr={page_idx}"
                try:
                    resp = context.request.get(page_url, timeout=15000)
                    if resp.status != 200:
                        break
                    html = resp.text()
                except Exception as e:
                    issues.append(fetch_issue(source_site=SOURCE_NAME, url=page_url, reason=REASON_FETCH_ERROR, message=str(e)))
                    break

                soup = BeautifulSoup(html, "html.parser")
                prod_divs = soup.select("div[data-product-id]")
                if not prod_divs:
                    break

                for div in prod_divs:
                    p_id = div.get("data-product-id", "")
                    raw_title = div.get("data-name", "")
                    prod_url = div.get("data-url", "")

                    if not prod_url or p_id in seen_product_ids:
                        continue
                    seen_product_ids.add(p_id)

                    # Récupération de la fiche pour le prix exact et les microdonnées
                    try:
                        p_resp = context.request.get(prod_url, timeout=12000)
                        if p_resp.status != 200:
                            continue
                        p_html = p_resp.text()
                    except Exception:
                        continue

                    p_soup = BeautifulSoup(p_html, "html.parser")
                    price_val: float | None = None
                    availability = "InStock"

                    for s in p_soup.find_all("script", type="application/ld+json"):
                        try:
                            d = json.loads(s.get_text())
                            if d.get("@type") == "Product":
                                offers = d.get("offers", {})
                                if isinstance(offers, dict):
                                    p_raw = offers.get("price")
                                    if p_raw:
                                        price_val = float(str(p_raw).replace(",", "."))
                                    avail = offers.get("availability", "")
                                    if "OutOfStock" in str(avail):
                                        availability = "OutOfStock"
                                break
                        except Exception:
                            continue

                    if not price_val or price_val <= 0:
                        # Fallback recherche regex dans la page
                        m = re.search(r'itemprop="price"\s+content="(\d+[\.,]?\d*)"', p_html)
                        if m:
                            try:
                                price_val = float(m.group(1).replace(",", "."))
                            except ValueError:
                                pass

                    if not price_val or price_val <= 0:
                        continue

                    # Stockage
                    storage_gb: int | None = None
                    storage_raw: str | None = None
                    sm = STORAGE_RE.search(raw_title)
                    if sm:
                        storage_raw = sm.group(0).strip()
                        try:
                            storage_gb = int(sm.group(1))
                        except ValueError:
                            pass

                    # Marque
                    title_lower = raw_title.lower()
                    if "iphone" in title_lower or "apple" in title_lower or "macbook" in title_lower or "ipad" in title_lower:
                        brand = "Apple"
                    elif "galaxy" in title_lower or "samsung" in title_lower:
                        brand = "Samsung"
                    elif "pixel" in title_lower or "google" in title_lower:
                        brand = "Google"
                    elif "xiaomi" in title_lower or "redmi" in title_lower:
                        brand = "Xiaomi"
                    elif "sony" in title_lower:
                        brand = "Sony"
                    elif "huawei" in title_lower:
                        brand = "Huawei"
                    else:
                        brand = raw_title.split()[0] if raw_title else "asgoodasnew"

                    model = raw_title
                    for remove_word in ["Apple", "Samsung", "Google", "Xiaomi", "Sony", "Huawei"]:
                        if model.startswith(remove_word):
                            model = model[len(remove_word):].strip()

                    row = {
                        "source_site": SOURCE_DOMAIN,
                        "source_url": prod_url.split("?")[0],
                        "source": prod_url.split("?")[0],
                        "timestamp": scrape_started_at,
                        "scrape_started_at": scrape_started_at,
                        "data_type": "product_sale",
                        "device_type": dev_type,
                        "device_family": dev_family,
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
                        "seller_name": "asgoodasnew France",
                        "shipping_cost": 0.0,
                        "warranty": "Garantie 30 mois reconditionné certifié",
                        "availability": availability,
                        "scraped_at": scrape_started_at,
                    }
                    rows.append(row)

                    if max_models and len(rows) >= max_models:
                        break

                if max_models and len(rows) >= max_models:
                    break

            if max_models and len(rows) >= max_models:
                break

        browser.close()

    initial_count = len(rows)
    rows = dedupe_dict_rows(rows, keys=("source_site", "brand", "model", "product_id", "price"))
    print(f"[{SOURCE_NAME}] Total lignes brutes: {initial_count}, après déduplication: {len(rows)}")

    clean_report = None
    fetch_errors = len(issues)
    if apply_cleaning and rows:
        cleaning_cfg = CleaningConfig(
            price_min=10.0,
            price_max=5000.0,
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

    print(f"[{SOURCE_NAME.upper()}] ✅ Fin d'exécution : {len(rows)} offres exportées dans {out_dir}", flush=True)

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
    parser.add_argument("--headful", action="store_true", help="Lancer en mode avec interface visible")
    args = parser.parse_args()

    scrape(
        output_dir=args.output_dir,
        apply_cleaning=not args.no_clean,
        emit_run_report=not args.no_report,
        max_models=args.max_models,
        headless=not args.headful,
    )


if __name__ == "__main__":
    main()
