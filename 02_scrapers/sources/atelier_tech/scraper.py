"""Atelier Tech Scraper (https://atelier-tech.net).

Atelier de réparation français spécialisé smartphones, tablettes et informatique (Angers / Envoi postal France) :
- Univers : repair_price (Grille tarifaire multi-marques et multi-pannes avec double niveau de prix : Sur place et Envoi postal)
- URL officielle : https://atelier-tech.net/reparation
- Marques couvertes : Apple, Samsung, Xiaomi, Google, Huawei, Oppo, Honor, OnePlus, Sony.
- Données : Écran (OEM vs Atelier), Batterie (OEM, Longue durée, Normal), Connecteur de charge, Caméra,
  Façade arrière, Boutons, Haut-parleur, Carte mère, Désoxydation, etc.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any

import aiohttp
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

SOURCE_NAME = "atelier_tech"
SOURCE_DOMAIN = "atelier-tech.net"
BASE_URL = "https://atelier-tech.net"
REPAIR_URL = "https://atelier-tech.net/reparation"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

TARGET_BRANDS = [
    ("apple", "Apple"),
    ("samsung", "Samsung"),
    ("xiaomi", "Xiaomi"),
    ("google", "Google"),
    ("huawei", "Huawei"),
    ("oppo", "Oppo"),
    ("honor", "Honor"),
    ("oneplus", "OnePlus"),
]


def map_repair_category(text: str) -> str:
    t = text.lower()
    if any(k in t for k in ["écran", "ecran", "vitre tactile", "afficheur"]):
        return "ecran"
    if any(k in t for k in ["batterie", "pile", "battery"]):
        return "batterie"
    if any(k in t for k in ["connecteur", "charge", "usb", "lightning"]):
        return "connecteur_charge"
    if any(k in t for k in ["caméra", "camera", "photo", "optique", "lentille"]):
        return "camera"
    if any(k in t for k in ["coque", "façade arrière", "facade arriere", "vitre arrière", "vitre arriere", "châssis", "chassis"]):
        return "arriere"
    if any(k in t for k in ["haut-parleur", "écouteur", "micro", "son"]):
        return "haut_parleur"
    if any(k in t for k in ["bouton", "vibreur", "nappe volume", "power"]):
        return "boutons"
    if any(k in t for k in ["carte mère", "carte mere", "micro-soudure"]):
        return "carte_mere"
    if any(k in t for k in ["eau", "désoxydation", "desoxydation", "oxydé"]):
        return "desoxydation"
    if any(k in t for k in ["données", "donnees", "sauvegarde", "logiciel"]):
        return "logiciel"
    return "autre"


def clean_model_name(title: str, brand_name: str) -> str:
    cleaned = title
    cleaned = re.sub(r"^(?:Réparation|Tarifs|Prix)\s+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(rf"^{brand_name}\s+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+à\s+Angers.*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*\|\s*Atelier Tech.*$", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


async def discover_model_urls(session: aiohttp.ClientSession) -> list[tuple[str, str, str]]:
    models: list[tuple[str, str, str]] = []
    for brand_slug, brand_name in TARGET_BRANDS:
        url = f"{BASE_URL}/reparation/{brand_slug}?type=smartphone"
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    continue
                html = await resp.text()
                soup = BeautifulSoup(html, "html.parser")
                for a in soup.find_all("a", href=True):
                    href = a["href"]
                    if href.startswith(f"/reparation/{brand_slug}/") and not href.endswith("/angers"):
                        full_url = f"{BASE_URL}{href}"
                        models.append((brand_slug, brand_name, full_url))
        except Exception as e:
            print(f"[{SOURCE_NAME}] Erreur découverte {brand_name}: {e}")

    seen = set()
    unique_models = []
    for b_slug, b_name, m_url in models:
        if m_url not in seen:
            seen.add(m_url)
            unique_models.append((b_slug, b_name, m_url))
    return unique_models


def parse_model_page(
    html: str,
    brand_name: str,
    page_url: str,
    scraped_at: str,
) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.find("h1")
    title = h1.get_text(strip=True) if h1 else ""
    if not title:
        head_title = soup.find("title")
        title = head_title.get_text(strip=True) if head_title else ""

    model = clean_model_name(title, brand_name)
    if not model:
        model = page_url.split("/")[-1].replace("-", " ").title()

    results: list[dict[str, Any]] = []

    accordion_items = soup.find_all("div", attrs={"data-slot": "accordion-item"})
    for div in accordion_items:
        title_el = div.find("span", class_=lambda c: c and "text-base font-medium" in c)
        if not title_el:
            continue
        repair_title = title_el.get_text(strip=True)
        if not repair_title:
            continue

        for tr in div.find_all("tr"):
            tds = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
            if len(tds) < 2 or not any("€" in x for x in tds):
                continue

            col0 = tds[0]
            stock = (
                "En stock"
                if "en stock" in col0.lower()
                else ("Sur commande" if "sur commande" in col0.lower() else "Disponible")
            )
            quality = re.sub(r"\s*(?:En stock|Sur commande|Disponible)\s*", "", col0, flags=re.IGNORECASE).strip()

            m_price = re.search(r"(\d+[\.,]?\d*)\s*€", tds[1])
            if not m_price:
                continue
            try:
                price_val = float(m_price.group(1).replace(",", "."))
            except ValueError:
                continue
            if price_val <= 0:
                continue

            price_postal = None
            if len(tds) > 2:
                m_postal = re.search(r"(\d+[\.,]?\d*)\s*€", tds[2])
                if m_postal:
                    try:
                        price_postal = float(m_postal.group(1).replace(",", "."))
                    except ValueError:
                        pass

            repair_raw = f"{repair_title} ({quality})" if quality else repair_title
            canonical_cat = map_repair_category(repair_title)
            item_name = f"{repair_raw} {brand_name} {model}".strip()

            delay_str = "Sur place en atelier (30-60 min) ou par envoi postal"
            if price_postal:
                delay_str += f" (Tarif correspondance postale : {price_postal:.2f} €)".replace(".", ",")

            row: dict[str, Any] = {
                "source_site": SOURCE_DOMAIN,
                "source_url": page_url,
                "source": page_url,
                "timestamp": scraped_at,
                "scrape_started_at": scraped_at,
                "data_type": "repair_price",
                "device_type": "smartphone" if "ipad" not in model.lower() and "tab" not in model.lower() else "tablet",
                "device_family": "Smartphone" if "ipad" not in model.lower() and "tab" not in model.lower() else "Tablette",
                "brand": brand_name,
                "model": model,
                "model_normalized": f"{brand_name} {model}".strip(),
                "item_name": item_name,
                "repair_category": canonical_cat,
                "repair_type": canonical_cat,
                "repair_type_raw": repair_raw,
                "price": price_val,
                "price_raw": f"{price_val:.2f} €".replace(".", ","),
                "currency": "EUR",
                "currency_symbol": "€",
                "part_quality": quality if quality else "Standard",
                "stock_status": stock,
                "workshop_name": "Atelier Tech Angers",
                "seller_name": "Atelier Tech",
                "delay": delay_str,
                "warranty": "Garantie pièces et main-d'œuvre comprises (Atelier agréé)",
                "scraped_at": scraped_at,
            }
            results.append(row)

    return results


async def crawl_all_models(
    model_targets: list[tuple[str, str, str]],
    scraped_at: str,
    concurrency: int = 15,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    results: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []

    sem = asyncio.Semaphore(concurrency)
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "fr-FR,fr;q=0.9"}
    timeout = aiohttp.ClientTimeout(total=15)
    connector = aiohttp.TCPConnector(limit=concurrency)

    async with aiohttp.ClientSession(headers=headers, timeout=timeout, connector=connector) as session:
        async def fetch_one(b_slug: str, b_name: str, m_url: str):
            async with sem:
                try:
                    async with session.get(m_url) as resp:
                        if resp.status == 200:
                            html = await resp.text()
                            rows = parse_model_page(html, b_name, m_url, scraped_at)
                            results.extend(rows)
                        elif resp.status != 404:
                            issues.append(
                                fetch_issue(
                                    source_site=SOURCE_NAME,
                                    url=m_url,
                                    reason=REASON_FETCH_ERROR,
                                    message=f"HTTP {resp.status}",
                                )
                            )
                except Exception as e:
                    issues.append(
                        fetch_issue(
                            source_site=SOURCE_NAME,
                            url=m_url,
                            reason=REASON_FETCH_ERROR,
                            message=str(e),
                        )
                    )

        tasks = [fetch_one(b_slug, b_name, m_url) for b_slug, b_name, m_url in model_targets]
        await asyncio.gather(*tasks)

    return results, issues


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

    print(f"[{SOURCE_NAME}] Découverte des modèles sur {REPAIR_URL}...")
    t0 = time.time()

    async def run_discovery():
        headers = {"User-Agent": USER_AGENT}
        async with aiohttp.ClientSession(headers=headers) as session:
            return await discover_model_urls(session)

    all_models = asyncio.run(run_discovery())
    print(f"[{SOURCE_NAME}] {len(all_models)} modèles smartphones découverts en {time.time()-t0:.1f}s.")

    if max_models and max_models > 0:
        model_targets = all_models[:max_models]
    else:
        model_targets = all_models[:250]

    print(f"[{SOURCE_NAME}] Lancement du scraping de {len(model_targets)} modèles...")
    t1 = time.time()
    rows, issues = asyncio.run(crawl_all_models(model_targets, scrape_started_at, concurrency=15))
    print(f"[{SOURCE_NAME}] {len(rows)} forfaits bruts collectés en {time.time()-t1:.1f}s.")

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
    parser = argparse.ArgumentParser(description=f"Scraper {SOURCE_NAME}")
    parser.add_argument("--output-dir", type=str, default=None)
    parser.add_argument("--max-models", type=int, default=None)
    args = parser.parse_args()
    scrape(output_dir=args.output_dir, max_models=args.max_models)


if __name__ == "__main__":
    main()
