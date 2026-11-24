"""C&C Scraper (https://www.cec.fr).

Apple Premium Partner France :
- Univers : repair_price (Tarifs officiels de réparation agréés Apple)
- Modèles : iPhone, iPad, Apple Watch, AirPods
- Sélecteur interactif select.superfields-dropdown via Playwright
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any
from playwright.async_api import async_playwright

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

SOURCE_NAME = "cec"
SOURCE_DOMAIN = "cec.fr"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)

CATEGORIES = [
    {
        "name": "iPhone",
        "url": "https://www.cec.fr/reparation-iphone",
        "device_type": "smartphone",
        "device_family": "Smartphone",
    },
    {
        "name": "iPad",
        "url": "https://www.cec.fr/reparation-ipad",
        "device_type": "tablette",
        "device_family": "Tablette",
    },
    {
        "name": "Apple Watch",
        "url": "https://www.cec.fr/reparation-apple-watch",
        "device_type": "smartwatch",
        "device_family": "Montre connectée",
    },
    {
        "name": "AirPods",
        "url": "https://www.cec.fr/reparation-airpods",
        "device_type": "airpods",
        "device_family": "Accessoire / Audio",
    },
]

CANONICAL_REPAIR_MAP = {
    "remplacement écran": "Écran",
    "remplacement batterie": "Batterie",
    "remplacement caméra arrière": "Caméra arrière",
    "remplacement truedepth caméra": "Caméra avant",
    "remplacement appareil": "Remplacement complet",
    "remplacement dispositif": "Remplacement complet",
    "diagnostic et devis": "Diagnostic",
    "restauration": "Logiciel / Restauration",
    "sauvegarde et transfert de données": "Sauvegarde / Transfert",
    "remplacement airpod": "Écouteur unitaire",
    "remplacement boîtier de charge": "Boîtier de charge",
}


def map_repair_category(raw_title: str) -> str:
    cleaned = raw_title.strip().lower()
    for pattern, canonical in CANONICAL_REPAIR_MAP.items():
        if pattern in cleaned:
            return canonical
    if "écran" in cleaned or "ecran" in cleaned:
        return "Écran"
    if "batterie" in cleaned:
        return "Batterie"
    if "caméra" in cleaned or "camera" in cleaned:
        return "Caméra"
    return "Autre réparation"


JS_EXTRACT_CARDS = """() => {
    const res = [];
    const all = document.querySelectorAll('*');
    for (const el of all) {
        if (el.children.length === 0 && el.textContent.includes('€')) {
            const gp = el.parentElement ? el.parentElement.parentElement : null;
            if (gp && gp.innerText.trim().length < 150) {
                res.push(gp.innerText.trim());
            }
        }
    }
    return [...new Set(res)];
}"""


async def scrape_async(
    max_models: int | None = None,
    issues: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    scraped_at = now_iso_utc()

    print(f"[{SOURCE_NAME}] Lancement du navigateur Playwright headless...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=USER_AGENT, viewport={"width": 1280, "height": 800})
        page = await context.new_page()

        total_models = 0
        for cat in CATEGORIES:
            url = cat["url"]
            print(f"[{SOURCE_NAME}] Navigation {cat['name']} ({url})...")
            try:
                await page.goto(url, wait_until="networkidle", timeout=30000)
            except Exception as e:
                print(f"[{SOURCE_NAME}] Erreur goto {url}: {e}")
                if issues is not None:
                    issues.append(fetch_issue(source_site=SOURCE_NAME, url=url, reason=REASON_FETCH_ERROR, message=str(e)))
                continue

            select = await page.query_selector("select.superfields-dropdown")
            if not select:
                continue

            opts = await select.query_selector_all("option")
            valid_options = []
            for o in opts:
                v = await o.get_attribute("value")
                t = (await o.inner_text()).strip()
                if t and "sélectionner" not in t.lower():
                    valid_options.append((v, t))

            print(f"[{SOURCE_NAME}] {len(valid_options)} modèles pour {cat['name']}.")

            for val, model_name in valid_options:
                if max_models and total_models >= max_models:
                    break

                try:
                    await page.select_option("select.superfields-dropdown", value=val)
                    await page.wait_for_timeout(250)

                    card_texts = await page.evaluate(JS_EXTRACT_CARDS)
                    for txt in card_texts:
                        lines = [l.strip() for l in txt.splitlines() if l.strip()]
                        if len(lines) == 2 and "€" in lines[1] and "€" not in lines[0]:
                            raw_title = lines[0]
                            price_str = lines[1].replace(" ", " ").strip()
                            m = re.search(r"(\d+[\.,]?\d*)\s*€", price_str)
                            if not m:
                                continue
                            try:
                                price_val = float(m.group(1).replace(",", "."))
                            except ValueError:
                                continue

                            if price_val <= 0:
                                continue

                            canonical_cat = map_repair_category(raw_title)
                            item_name = f"{raw_title} Apple {model_name}".strip()

                            row = {
                                "source_site": SOURCE_DOMAIN,
                                "source_url": url,
                                "source": url,
                                "timestamp": scraped_at,
                                "scrape_started_at": scraped_at,
                                "data_type": "repair_price",
                                "device_type": cat["device_type"],
                                "device_family": cat["device_family"],
                                "brand": "Apple",
                                "model": model_name,
                                "model_normalized": f"Apple {model_name}",
                                "item_name": item_name,
                                "repair_category": canonical_cat,
                                "repair_type": canonical_cat,
                                "repair_type_raw": raw_title,
                                "price": price_val,
                                "price_raw": f"{price_val:.2f} €".replace(".", ","),
                                "currency": "EUR",
                                "currency_symbol": "€",
                                "workshop_name": "C&C France (Apple Premium Partner)",
                                "seller_name": "C&C France",
                                "delay": "Sur rendez-vous en centre de services agréé Apple",
                                "warranty": "Garantie officielle constructeur Apple",
                                "scraped_at": scraped_at,
                            }
                            rows.append(row)

                    total_models += 1
                except Exception as err:
                    print(f"[{SOURCE_NAME}] Erreur modèle {model_name}: {err}")

        await browser.close()

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

    rows = asyncio.run(scrape_async(max_models=max_models, issues=issues))

    initial_count = len(rows)
    rows = dedupe_dict_rows(rows, keys=("source_site", "brand", "model", "repair_type_raw", "price"))
    print(f"[{SOURCE_NAME}] Total lignes brutes: {initial_count}, après déduplication: {len(rows)}")

    clean_report = None
    fetch_errors = len(issues)
    if apply_cleaning and rows:
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
