"""Smart Repair 63 Scraper (https://smartrepair63.fr/tarifs.html).

Grille tarifaire complète de réparation smartphones et tablettes à Clermont-Ferrand :
Stratégie ordonnée (Roadmap E-commerce AI Scraping) :
  2. Parsing HTML Direct (BeautifulSoup / Requests) :
     - Découverte des 500+ modèles sur les 19 pages de séries et marques
       (iPhone, iPad, Samsung Séries S/A/J/M/Note/Z, Xiaomi, Oppo, Huawei, Honor, Google Pixel, etc.)
     - Extraction conjointe des cartes modèles et de leurs pages détails dédiées (.price-row)
     - Normalisation canonique des pannes (Écran, Batterie, Connecteur, Vitre Arrière, Caméra)
     - Export standard E-commerce AI Scraping (5 fichiers)
"""

from __future__ import annotations

import sys
from pathlib import Path
_SCRAPERS_ROOT = Path(__file__).resolve().parents[2]
if str(_SCRAPERS_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRAPERS_ROOT))
_CORE_DIR = _SCRAPERS_ROOT / 'core'
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))

import argparse
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from tqdm import tqdm

from scraper.core.filtering import CleaningConfig, clean_dataset
from scraper.core.http_client import create_session
from scraper.core.normalization import normalize_repair_type
from scraper.core.parser_utils import (
    dedupe_dict_rows,
    now_iso_utc,
)
from scraper.core.row_export import write_jsonl_and_csv
from scraper.core.run_report import (
    REASON_FETCH_ERROR,
    fetch_issue,
    finalize_run_artifacts,
    summarize_run_line,
)

BASE_URL = "https://smartrepair63.fr/"
TARIFS_INDEX_URL = "https://smartrepair63.fr/tarifs.html"
SOURCE_NAME = "smart_repair_63"
SOURCE_DOMAIN = "smartrepair63.fr"

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

SHOP_INFO = {
    "shop_name": "Smart Repair 63",
    "address": "84 avenue Édouard Michelin",
    "city": "Clermont-Ferrand",
    "postal_code": "63100",
    "phone": "06 50 19 25 09",
    "delay": "Réparation en 30 à 60 minutes sans rendez-vous",
    "warranty": "Garantie 6 mois pièces et main d'œuvre",
}

SERIES_PAGES_CONFIG = [
    ("tarifs-iphone.html", "Apple", "Smartphone"),
    ("tarifs-xiaomi.html", "Xiaomi", "Smartphone"),
    ("tarifs-oppo.html", "Oppo", "Smartphone"),
    ("tarifs-huawei.html", "Huawei", "Smartphone"),
    ("tarifs-honor.html", "HONOR", "Smartphone"),
    ("tarifs-google.html", "Google", "Smartphone"),
    ("tarifs-oneplus.html", "OnePlus", "Smartphone"),
    ("tarifs-realme.html", "Realme", "Smartphone"),
    ("tarifs-nokia.html", "Nokia", "Smartphone"),
    ("tarifs-wiko.html", "Wiko", "Smartphone"),
    ("tarifs-motorola.html", "Motorola", "Smartphone"),
    ("tarifs-sony.html", "Sony", "Smartphone"),
    ("tarifs-ipad.html", "Apple", "Tablette"),
    ("tarifs-serie-s.html", "Samsung", "Smartphone"),
    ("tarifs-serie-a.html", "Samsung", "Smartphone"),
    ("tarifs-serie-j.html", "Samsung", "Smartphone"),
    ("tarifs-serie-m.html", "Samsung", "Smartphone"),
    ("tarifs-serie-note.html", "Samsung", "Smartphone"),
    ("tarifs-serie-z.html", "Samsung", "Smartphone"),
]

REPAIR_NORMALIZATION_MAP = {
    "remplacement ecran": "Ecran LCD",
    "ecran": "Ecran LCD",
    "écran": "Ecran LCD",
    "remplacement batterie": "Batterie",
    "batterie": "Batterie",
    "vitre arriere": "Façade arrière",
    "vitre arrière": "Façade arrière",
    "connecteur de charge": "Connecteur de charge",
    "module camera": "Appareil photo arrière",
    "camera": "Appareil photo arrière",
    "caméra": "Appareil photo arrière",
    "decontamination": "Désoxydation",
    "desoxydation": "Désoxydation",
}


def parse_price(text: str) -> float | None:
    """Extrait le montant numérique en euros depuis une chaîne."""
    match = re.search(r"(\d+(?:[.,]\d+)?)", text.replace(" ", ""))
    if not match:
        return None
    try:
        val = float(match.group(1).replace(",", "."))
        return val if val > 0 else None
    except ValueError:
        return None


def discover_all_models(session=None) -> list[dict[str, Any]]:
    """Parcourt les 19 pages de séries et marques pour découvrir tous les modèles."""
    if session is None:
        session = create_session()

    discovered: list[dict[str, Any]] = []
    seen_keys = set()

    for page_name, brand, dev_fam in SERIES_PAGES_CONFIG:
        url = urljoin(BASE_URL, page_name)
        try:
            resp = session.get(url, headers=DEFAULT_HEADERS, timeout=15)
            if resp.status_code != 200:
                continue
            resp.encoding = "utf-8"
            soup = BeautifulSoup(resp.text, "html.parser")
        except Exception as e:
            print(f"[{SOURCE_NAME}] Erreur lors de la lecture de {url}: {e}", flush=True)
            continue

        cards = soup.find_all(class_=lambda c: c and ("model-card" in c or "card" in c))
        for c in cards:
            m_div = c.find(class_=lambda x: x and ("model-name" in x or x == "model"))
            p_div = c.find(class_=lambda x: x and ("model-price" in x or x == "price"))
            if not m_div or not p_div:
                continue

            model_name = m_div.get_text(strip=True)
            price_text = p_div.get_text(strip=True)
            if not model_name or "€" not in price_text:
                continue

            base_screen_price = parse_price(price_text)
            href = c.get("href") or ""
            detail_url = urljoin(BASE_URL, href) if href else ""

            key = (brand, model_name)
            if key in seen_keys:
                continue
            seen_keys.add(key)

            discovered.append(
                {
                    "brand": brand,
                    "model": model_name,
                    "device_family": dev_fam,
                    "base_screen_price": base_screen_price,
                    "category_url": url,
                    "detail_url": detail_url,
                }
            )

    return discovered


def fetch_detail_page_repairs(detail_url: str, session=None) -> list[tuple[str, float]]:
    """Extrait les prestations tarifées détaillées (.price-row) depuis la page d'un modèle."""
    if not detail_url or not detail_url.endswith(".html"):
        return []

    if session is None:
        session = create_session()

    try:
        resp = session.get(detail_url, headers=DEFAULT_HEADERS, timeout=12)
        if resp.status_code != 200:
            return []
        resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception:
        return []

    results: list[tuple[str, float]] = []
    price_rows = soup.find_all(class_="price-row")
    for row in price_rows:
        lbl = row.find(class_="price-label")
        val = row.find(class_="price-val")
        if not lbl or not val:
            continue

        label_txt = lbl.get_text(strip=True)
        pval = parse_price(val.get_text(strip=True))
        if label_txt and pval and pval > 0:
            results.append((label_txt, pval))

    return results


def scrape(
    *,
    output_dir: str = str(_SCRAPERS_ROOT / "outputs"),
    apply_cleaning: bool = True,
    emit_run_report: bool = True,
    max_models: int | None = None,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    """Point d'entrée principal du scraper Smart Repair 63."""
    scrape_started_at = now_iso_utc()
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    timestamp = re.sub(r"[-:T]", "", scrape_started_at.split(".")[0]) + "0000"
    stem = f"smart_repair_63_repair_prices_{timestamp}"

    json_path = out_dir / f"{stem}.json"
    jsonl_path = out_dir / f"{stem}.jsonl"
    csv_path = out_dir / f"{stem}.csv"

    session = create_session()
    all_rows: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    fetch_errors = 0

    print(f"[{SOURCE_NAME}] Découverte des modèles sur smartrepair63.fr...", flush=True)
    models = discover_all_models(session=session)
    print(f"[{SOURCE_NAME}] {len(models)} modèles identifiés sur l'ensemble des marques.", flush=True)

    if max_models and max_models > 0:
        models = models[:max_models]
        print(f"[{SOURCE_NAME}] Limite active : {max_models} modèles.")

    scraped_at = now_iso_utc()

    def process_model(m_info: dict[str, Any]) -> list[dict[str, Any]]:
        th_session = create_session()
        detail_url = m_info["detail_url"]
        brand = m_info["brand"]
        model_name = m_info["model"]
        device_family = m_info["device_family"]
        base_price = m_info["base_screen_price"]
        source_page_url = detail_url if detail_url else m_info["category_url"]

        detail_repairs = fetch_detail_page_repairs(detail_url, session=th_session) if detail_url else []

        # Si pas de page détail ou aucune prestation trouvée, utiliser le tarif écran de la carte
        if not detail_repairs and base_price:
            detail_repairs = [("Remplacement ecran", base_price)]

        rows: list[dict[str, Any]] = []
        for raw_repair_name, price_val in detail_repairs:
            r_lower = raw_repair_name.lower().strip()
            if r_lower in REPAIR_NORMALIZATION_MAP:
                repair_type = REPAIR_NORMALIZATION_MAP[r_lower]
            else:
                canon, matched = normalize_repair_type(raw_repair_name)
                repair_type = canon if matched and canon else raw_repair_name

            rows.append(
                {
                    "source_site": SOURCE_DOMAIN,
                    "source_url": source_page_url,
                    "source": source_page_url,
                    "timestamp": scraped_at,
                    "data_type": "repair_price",
                    "device_family": device_family,
                    "brand": brand,
                    "model": model_name,
                    "item_name": f"{raw_repair_name} {model_name}",
                    "repair_type_raw": raw_repair_name,
                    "repair_type": repair_type,
                    "price": price_val,
                    "price_raw": f"{price_val} €",
                    "currency": "EUR",
                    "currency_symbol": "€",
                    "shop_name": SHOP_INFO["shop_name"],
                    "address": SHOP_INFO["address"],
                    "city": SHOP_INFO["city"],
                    "postal_code": SHOP_INFO["postal_code"],
                    "delay": SHOP_INFO["delay"],
                    "warranty": SHOP_INFO["warranty"],
                    "model_sku": "",
                    "repair_category": "Réparation en atelier",
                    "scraped_at": scraped_at,
                }
            )
        return rows

    with ThreadPoolExecutor(max_workers=16) as executor:
        futures = {executor.submit(process_model, m): m for m in models}
        for fut in tqdm(as_completed(futures), total=len(models), desc="Smart Repair 63 Extraction", unit="modèle"):
            m = futures[fut]
            try:
                res = fut.result()
                all_rows.extend(res)
            except Exception as e:
                fetch_errors += 1
                issues.append(
                    fetch_issue(
                        reason=REASON_FETCH_ERROR,
                        source_site=SOURCE_NAME,
                        source_url=m.get("detail_url") or m.get("category_url"),
                        detail=str(e),
                    )
                )

    deduped_rows = dedupe_dict_rows(
        all_rows,
        keys=["model", "repair_type_raw", "price", "shop_name"],
    )

    clean_report = None
    if apply_cleaning:
        cleaning_cfg = CleaningConfig(
            price_min=10.0,
            price_max=4000.0,
            dedupe_keys_repair_price=("source_url", "model", "repair_type_raw", "price_raw"),
        )
        deduped_rows, clean_report = clean_dataset(
            deduped_rows,
            data_type="repair_price",
            config=cleaning_cfg,
            cleaning_issues=issues if emit_run_report else None,
        )
        if clean_report:
            print(clean_report.summary_line(), flush=True)

    for row in deduped_rows:
        row.setdefault("scrape_started_at", scrape_started_at)

    write_jsonl_and_csv(deduped_rows, jsonl_path=jsonl_path, csv_path=csv_path)

    payload = {
        "source_site": SOURCE_NAME,
        "scrape_started_at": scrape_started_at,
        "row_count": len(deduped_rows),
        "rows": deduped_rows,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n[SMART REPAIR 63] Terminé avec succès. {len(deduped_rows)} tarifs exportés dans {out_dir}", flush=True)

    if emit_run_report:
        payload_report = finalize_run_artifacts(
            source_site=SOURCE_NAME,
            scrape_started_at=scrape_started_at,
            json_path=json_path,
            jsonl_path=jsonl_path,
            csv_path=csv_path,
            row_count=len(deduped_rows),
            apply_cleaning=apply_cleaning,
            clean_report=clean_report,
            fetch_errors=fetch_errors,
            issues=issues,
        )
        print(summarize_run_line(payload_report), flush=True)

    return deduped_rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Smart Repair 63 Scraper")
    parser.add_argument("--output-dir", default=str(_SCRAPERS_ROOT / "outputs"), help="Dossier de sortie")
    parser.add_argument("--no-clean", action="store_true", help="Désactiver le nettoyage")
    parser.add_argument("--no-report", action="store_true", help="Désactiver le rapport d'exécution")
    parser.add_argument("--max-models", type=int, default=None, help="Nombre max de modèles à scraper")
    args = parser.parse_args()

    scrape(
        output_dir=args.output_dir,
        apply_cleaning=not args.no_clean,
        emit_run_report=not args.no_report,
        max_models=args.max_models,
    )


if __name__ == "__main__":
    main()
