"""Clinique Phone Scraper (https://clinique-phone.com).

Atelier de réparation smartphones et multimédia à Rosny-sous-Bois (93) :
- 2 Allée Denis Papin, 93110 Rosny-sous-Bois
- Stratégie d'extraction :
    1. Navigation depuis la page d'accueil vers les 12 univers marques :
       Apple, Samsung, Xiaomi, Google Pixel, Huawei, Honor, Oppo, OnePlus, Realme, Motorola, Sony, Nokia.
    2. Extraction des fiches modèles et de la grille tarifaire complète (/tarifs?brand={brand}).
    3. Normalisation canonique des modèles et des prestations (Écran -> Ecran LCD, Batterie, Vitre arrière -> Façade arrière).
    4. Déduplication et standardisation selon le schéma d'artefacts E-commerce AI V2.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SCRAPERS_ROOT = Path(__file__).resolve().parents[2]
if str(_SCRAPERS_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRAPERS_ROOT))
if str(_SCRAPERS_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(_SCRAPERS_ROOT.parent))
_CORE_DIR = _SCRAPERS_ROOT / "core"
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))

import argparse
import json
import re
import urllib.parse
from typing import Any

from bs4 import BeautifulSoup

try:
    from scraper.core.filtering import CleaningConfig, clean_dataset
    from scraper.core.http_client import create_session
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
    from core.http_client import create_session
    from core.normalization import normalize_repair_type
    from core.parser_utils import dedupe_dict_rows, now_iso_utc
    from core.row_export import write_jsonl_and_csv
    from core.run_report import (
        REASON_FETCH_ERROR,
        fetch_issue,
        finalize_run_artifacts,
        summarize_run_line,
    )

BASE_URL = "https://clinique-phone.com"
TARIFS_URL = "https://clinique-phone.com/tarifs"
SOURCE_NAME = "clinique_phone"
SOURCE_DOMAIN = "clinique-phone.com"

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
}

SHOP_INFO = {
    "shop_name": "Clinique Phone Rosny-sous-Bois",
    "address": "2 Allée Denis Papin",
    "city": "Rosny-sous-Bois",
    "postal_code": "93110",
    "delay": "Intervention express en moins d'1h sans rendez-vous",
    "warranty": "Garantie pièces jusqu'à 12 mois",
}

# 12 marques couvertes par Clinique Phone
BRAND_CATALOG = [
    ("apple", "Apple", f"{TARIFS_URL}"),
    ("samsung", "Samsung", f"{TARIFS_URL}?brand=samsung"),
    ("xiaomi", "Xiaomi", f"{TARIFS_URL}?brand=xiaomi"),
    ("google", "Google", f"{TARIFS_URL}?brand=google"),
    ("huawei", "Huawei", f"{TARIFS_URL}?brand=huawei"),
    ("honor", "Honor", f"{TARIFS_URL}?brand=honor"),
    ("oppo", "Oppo", f"{TARIFS_URL}?brand=oppo"),
    ("oneplus", "OnePlus", f"{TARIFS_URL}?brand=oneplus"),
    ("realme", "Realme", f"{TARIFS_URL}?brand=realme"),
    ("motorola", "Motorola", f"{TARIFS_URL}?brand=motorola"),
    ("sony", "Sony", f"{TARIFS_URL}?brand=sony"),
    ("nokia", "Nokia", f"{TARIFS_URL}?brand=nokia"),
]

REPAIR_NORMALIZATION_MAP = {
    "écran": "Ecran LCD",
    "ecran": "Ecran LCD",
    "batterie": "Batterie",
    "vitre arrière": "Façade arrière",
    "vitre arriere": "Façade arrière",
    "façade arrière": "Façade arrière",
    "connecteur": "Connecteur de charge",
    "connecteur de charge": "Connecteur de charge",
    "caméra": "Appareil photo arrière",
    "camera": "Appareil photo arrière",
}


def format_model(raw_model: str, brand: str) -> str:
    """Normalise et préfixe proprement le modèle avec sa marque canonique si nécessaire."""
    m = re.sub(r"\s+", " ", raw_model).strip()
    b = brand.strip()

    if b.lower() == "apple":
        if not re.match(r"^(iPhone|Apple)", m, re.I):
            return f"iPhone {m}"
        return m
    if b.lower() == "samsung":
        if not re.match(r"^(Samsung|Galaxy)", m, re.I):
            return f"Samsung Galaxy {m}"
        if m.lower().startswith("galaxy"):
            return f"Samsung {m}"
        return m
    if b.lower() == "google":
        if not re.match(r"^(Google|Pixel)", m, re.I):
            return f"Google Pixel {m}"
        if m.lower().startswith("pixel"):
            return f"Google {m}"
        return m
    if b.lower() == "sony":
        if not re.match(r"^(Sony|Xperia)", m, re.I):
            return f"Sony Xperia {m}"
        if m.lower().startswith("xperia"):
            return f"Sony {m}"
        return m
    if not m.lower().startswith(b.lower()):
        return f"{b} {m}"
    return m


def parse_price(val: str) -> float | None:
    """Extrait un prix flottant positif ou None si sur devis / indisponible."""
    if not val:
        return None
    v = val.lower().strip()
    if any(k in v for k in ["devis", "-", "gratuit", "n/a", "non"]):
        return None
    m = re.search(r"(\d+(?:[.,]\d+)?)", v)
    if m:
        try:
            p = float(m.group(1).replace(",", "."))
            if p > 0:
                return p
        except ValueError:
            pass
    return None


def scrape(
    output_dir: str = str(_SCRAPERS_ROOT / "outputs"),
    apply_cleaning: bool = True,
    emit_run_report: bool = True,
    max_models: int | None = None,
) -> list[dict[str, Any]]:
    """Point d'entrée principal du scraper Clinique Phone."""
    scrape_started_at = now_iso_utc()
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    timestamp = re.sub(r"[-:T]", "", scrape_started_at.split(".")[0]) + "0000"
    stem = f"clinique_phone_repair_prices_{timestamp}"

    json_path = out_dir / f"{stem}.json"
    jsonl_path = out_dir / f"{stem}.jsonl"
    csv_path = out_dir / f"{stem}.csv"

    print(f"[{SOURCE_NAME}] Démarrage de l'extraction sur {SOURCE_DOMAIN}...", flush=True)

    session = create_session()
    session.headers.update(DEFAULT_HEADERS)

    issues: list[dict[str, Any]] = []
    fetch_errors = 0
    raw_offers: list[dict[str, Any]] = []

    # 1. Vérification de l'accessibilité de la page d'accueil
    try:
        home_resp = session.get(BASE_URL, timeout=12)
        if home_resp.status_code == 200:
            print(f"[{SOURCE_NAME}] Connexion réussie à {BASE_URL} (HTTP 200)", flush=True)
        else:
            print(f"[{SOURCE_NAME}] Avertissement accueil HTTP {home_resp.status_code}", flush=True)
    except Exception as e:
        print(f"[{SOURCE_NAME}] Note sur l'accueil : {e}", flush=True)

    # 2. Parcours des 12 univers marques
    print(f"[{SOURCE_NAME}] Parcours des {len(BRAND_CATALOG)} marques référencées...", flush=True)

    for slug, brand_name, brand_url in BRAND_CATALOG:
        try:
            resp = session.get(brand_url, timeout=15)
            if resp.status_code != 200:
                fetch_errors += 1
                issues.append(fetch_issue(brand_url, f"HTTP {resp.status_code}", REASON_FETCH_ERROR))
                print(f"[{SOURCE_NAME}] Erreur HTTP {resp.status_code} sur {brand_name}", flush=True)
                continue

            soup = BeautifulSoup(resp.text, "html.parser")
            cards = soup.find_all("a", href=lambda h: h and "/reservation?" in h and "model=" in h)

            brand_count = 0
            for card in cards:
                # Extraction du nom de modèle
                parsed = urllib.parse.urlparse(card["href"])
                qs = urllib.parse.parse_qs(parsed.query)
                raw_model = qs.get("model", [""])[0].strip()
                if not raw_model:
                    h3 = card.find("h3")
                    raw_model = h3.get_text(strip=True) if h3 else ""

                if not raw_model:
                    continue

                canonical_model = format_model(raw_model, brand_name)

                # Extraction des couples (prestation, tarif)
                spans = [s.get_text(strip=True) for s in card.find_all("span")]
                for i in range(0, len(spans) - 1, 2):
                    raw_repair = spans[i].strip()
                    raw_price_str = spans[i + 1].strip()

                    price = parse_price(raw_price_str)
                    if price is not None:
                        norm_repair = REPAIR_NORMALIZATION_MAP.get(raw_repair.lower())
                        if not norm_repair:
                            canon, matched = normalize_repair_type(raw_repair)
                            norm_repair = canon if matched and canon else raw_repair

                        raw_offers.append({
                            "brand": brand_name,
                            "model": canonical_model,
                            "item_name": f"{raw_repair} {canonical_model}",
                            "repair_type_raw": raw_repair,
                            "repair_type": norm_repair,
                            "price": price,
                            "price_raw": raw_price_str if "€" in raw_price_str else f"{int(price)} €",
                            "source_url": brand_url,
                        })
                        brand_count += 1

                if max_models and len(raw_offers) >= max_models:
                    break

            print(f"[{SOURCE_NAME}] {brand_name:<10}: {len(cards)} modèles, {brand_count} tarifs extraits.", flush=True)

        except Exception as e:
            fetch_errors += 1
            issues.append(fetch_issue(brand_url, f"Erreur {brand_name}: {e}", REASON_FETCH_ERROR))
            print(f"[{SOURCE_NAME}] Erreur sur {brand_name}: {e}", flush=True)

        if max_models and len(raw_offers) >= max_models:
            break

    print(f"[{SOURCE_NAME}] Total brut : {len(raw_offers)} tarifs extraits.", flush=True)

    # 3. Standardisation des enregistrements E-commerce AI Scraping
    scraped_at = now_iso_utc()
    all_rows: list[dict[str, Any]] = []
    for item in raw_offers:
        all_rows.append({
            "source_site": SOURCE_DOMAIN,
            "source_url": item["source_url"],
            "source": item["source_url"],
            "timestamp": scraped_at,
            "data_type": "repair_price",
            "device_family": "Smartphone",
            "brand": item["brand"],
            "model": item["model"],
            "item_name": item["item_name"],
            "repair_type_raw": item["repair_type_raw"],
            "repair_type": item["repair_type"],
            "price": item["price"],
            "price_raw": item["price_raw"],
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
        })

    # Déduplication
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

    print(f"\n[CLINIQUE PHONE] Terminé avec succès : {len(deduped_rows)} tarifs exportés dans {out_dir}", flush=True)

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
    parser = argparse.ArgumentParser(description="Clinique Phone Scraper (Rosny-sous-Bois)")
    parser.add_argument("--output-dir", default=str(_SCRAPERS_ROOT / "outputs"), help="Dossier de sortie")
    parser.add_argument("--no-clean", action="store_true", help="Désactiver le nettoyage")
    parser.add_argument("--no-report", action="store_true", help="Désactiver le rapport d'exécution")
    parser.add_argument("--max-models", type=int, default=None, help="Nombre max d'offres extraites")
    args = parser.parse_args()

    scrape(
        output_dir=args.output_dir,
        apply_cleaning=not args.no_clean,
        emit_run_report=not args.no_report,
        max_models=args.max_models,
    )


if __name__ == "__main__":
    main()
