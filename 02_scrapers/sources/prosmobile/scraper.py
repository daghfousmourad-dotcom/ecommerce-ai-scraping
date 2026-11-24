"""ProsMobile Scraper (https://prosmobile.fr).

Atelier de réparation smartphones et multimédia à Strasbourg / Schiltigheim :
- C.C. Leclerc, 90 route du Général de Gaulle, 67300 Schiltigheim
- Stratégie d'extraction hybride ultra-complète :
    1. Scraping des 210 pages modèles détaillées (/reparations/{brand}/{model})
       pour Apple, Samsung, Huawei, Xiaomi, Google Pixel, OnePlus, Honor, Motorola.
    2. Scraping de la grille tarifaire complète (/tarifs)
       couvrant l'ensemble des smartphones (iPhone, Samsung Galaxy, et 'Autres' :
       Honor, Motorola, Oppo, Realme, Xiaomi/Poco, OnePlus, Google Pixel).
    3. Déduplication et standardisation selon le schéma d'artefacts E-commerce AI V2.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SCRAPERS_ROOT = Path(__file__).resolve().parents[2]
if str(_SCRAPERS_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRAPERS_ROOT))
_CORE_DIR = _SCRAPERS_ROOT / "core"
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))

import argparse
import concurrent.futures
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

BASE_PAGE_URL = "https://prosmobile.fr/tarifs"
REPARATIONS_URL = "https://prosmobile.fr/reparations"
SOURCE_NAME = "prosmobile"
SOURCE_DOMAIN = "prosmobile.fr"

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

SHOP_INFO = {
    "shop_name": "ProsMobile Strasbourg",
    "address": "90 route du Général de Gaulle - C.C. Leclerc",
    "city": "Schiltigheim",
    "postal_code": "67300",
    "delay": "Intervention express en 30 min sur place",
    "warranty": "Garantie 6 mois à 1 an selon la pièce",
}

BRAND_HUB_SLUGS = [
    "iphone",
    "samsung",
    "huawei",
    "xiaomi",
    "pixel",
    "oneplus",
    "honor",
    "motorola",
]

REPAIR_NORMALIZATION_MAP = {
    "ecran": "Ecran LCD",
    "écran": "Ecran LCD",
    "ecran oled": "Ecran OLED",
    "ecran lcd": "Ecran LCD",
    "batterie": "Batterie",
    "connecteur de charge": "Connecteur de charge",
    "camera arriere": "Appareil photo arrière",
    "caméra arrière": "Appareil photo arrière",
    "camera avant": "Appareil photo avant",
    "vitre arriere": "Façade arrière",
    "vitre arrière": "Façade arrière",
    "chassis arriere": "Châssis",
    "lentille camera": "Lentille caméra",
    "lentille camera serie": "Lentille caméra",
    "bouton power/ volume/ flash/ vibreur": "Bouton Power / Volume",
    "bouton power / volume / flash / vibreur": "Bouton Power / Volume",
    "ecouteur / haut parleur interne": "Ecouteur / Haut-parleur",
    "ecouteur /haut parleur interne": "Ecouteur / Haut-parleur",
    "ecouteur/ haut parleur interne": "Ecouteur / Haut-parleur",
}


def detect_brand(text: str) -> str:
    """Détecte la marque canonique à partir d'une chaîne textuelle."""
    t = text.upper()
    if any(k in t for k in ["IPHONE", "APPLE"]):
        return "Apple"
    if any(k in t for k in ["SAMSUNG", "GALAXY"]):
        return "Samsung"
    if "HUAWEI" in t:
        return "Huawei"
    if any(k in t for k in ["XIAOMI", "REDMI", "POCO"]):
        return "Xiaomi"
    if any(k in t for k in ["PIXEL", "GOOGLE"]):
        return "Google"
    if any(k in t for k in ["ONEPLUS", "ONE PLUS"]):
        return "OnePlus"
    if "HONOR" in t:
        return "Honor"
    if "OPPO" in t:
        return "Oppo"
    if any(k in t for k in ["MOTOROLA", "MOTO"]):
        return "Motorola"
    if "REALME" in t:
        return "Realme"
    return "Autre"


def format_model(h3: str, btn: str) -> tuple[str, str]:
    """Formate et associe la marque et le modèle complet pour chaque déclinaison."""
    h3_clean = h3.strip()
    btn_clean = btn.strip()
    upper_h3 = h3_clean.upper()

    if "IPHONE" in upper_h3:
        brand = "Apple"
        model = btn_clean if btn_clean.lower().startswith("iphone") else f"iPhone {btn_clean}"
        return brand, model
    if "SAMSUNG" in upper_h3:
        brand = "Samsung"
        if btn_clean.lower().startswith("samsung"):
            model = btn_clean
        elif btn_clean.lower().startswith("galaxy"):
            model = f"Samsung {btn_clean}"
        else:
            model = f"Samsung Galaxy {btn_clean}"
        return brand, model
    if "HUAWEI" in upper_h3:
        return "Huawei", btn_clean if btn_clean.lower().startswith("huawei") else f"Huawei {btn_clean}"
    if "PIXEL" in upper_h3 or "GOOGLE" in upper_h3:
        return "Google", btn_clean if btn_clean.lower().startswith("google") else f"Google {btn_clean}"
    if "MOTOROLA" in upper_h3:
        return "Motorola", btn_clean if btn_clean.lower().startswith("motorola") else f"Motorola {btn_clean}"
    if "OPPO" in upper_h3:
        return "Oppo", btn_clean if btn_clean.lower().startswith("oppo") else f"Oppo {btn_clean}"
    if any(k in upper_h3 for k in ["XIAOMI", "REDMI", "POCO"]):
        return "Xiaomi", btn_clean if btn_clean.lower().startswith("xiaomi") else f"Xiaomi {btn_clean}"
    if "ONEPLUS" in upper_h3 or "ONE PLUS" in upper_h3:
        return "OnePlus", btn_clean if btn_clean.lower().startswith("oneplus") else f"OnePlus {btn_clean}"
    if "HONOR" in upper_h3:
        return "Honor", btn_clean if btn_clean.lower().startswith("honor") else f"Honor {btn_clean}"
    if "REALME" in upper_h3:
        return "Realme", btn_clean if btn_clean.lower().startswith("realme") else f"Realme {btn_clean}"
    return "Autre", f"{h3_clean} {btn_clean}".strip()


def extract_subpage(item: tuple[str, str], session: Any) -> list[dict[str, Any]]:
    """Extrait les prestations tarifées d'une page modèle individuelle."""
    brand_slug, page_url = item
    rows: list[dict[str, Any]] = []
    try:
        resp = session.get(page_url, timeout=10)
        if resp.status_code != 200:
            return rows
        soup = BeautifulSoup(resp.text, "html.parser")
        h1 = soup.find("h1")
        raw_title = h1.get_text(" ", strip=True) if h1 else ""
        m = re.search(r"Réparation\s+(.*?)\s+à\s+Strasbourg", raw_title, re.I)
        model_name = m.group(1).strip() if m else raw_title
        if not model_name:
            model_name = page_url.split("/")[-1].replace("-", " ").title()

        brand = detect_brand(f"{model_name} {brand_slug}")
        cards = soup.find_all("div", class_=lambda c: c and "rounded-xl" in c and "bg-slate-50" in c)

        for card in cards:
            txt = card.get_text(" | ", strip=True)
            pm = re.search(r"^(.*?)\s*\|\s*(\d{2,3})\s*€$", txt)
            if pm:
                piece_raw = pm.group(1).strip()
                price = float(pm.group(2))
                if price <= 0:
                    continue

                norm_key = piece_raw.lower()
                repair_type = REPAIR_NORMALIZATION_MAP.get(norm_key)
                if not repair_type:
                    canon, matched = normalize_repair_type(piece_raw)
                    repair_type = canon if matched and canon else piece_raw

                rows.append({
                    "brand": brand,
                    "model": model_name,
                    "item_name": f"{piece_raw} {model_name}",
                    "repair_type_raw": piece_raw,
                    "repair_type": repair_type,
                    "price": price,
                    "source_url": page_url,
                })
    except Exception:
        pass
    return rows


def scrape(
    output_dir: str = str(_SCRAPERS_ROOT / "outputs"),
    apply_cleaning: bool = True,
    emit_run_report: bool = True,
    max_models: int | None = None,
) -> list[dict[str, Any]]:
    """Point d'entrée principal du scraper ProsMobile."""
    scrape_started_at = now_iso_utc()
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    timestamp = re.sub(r"[-:T]", "", scrape_started_at.split(".")[0]) + "0000"
    stem = f"prosmobile_repair_prices_{timestamp}"

    json_path = out_dir / f"{stem}.json"
    jsonl_path = out_dir / f"{stem}.jsonl"
    csv_path = out_dir / f"{stem}.csv"

    print(f"[{SOURCE_NAME}] Démarrage du scraping hybride sur {SOURCE_DOMAIN}...", flush=True)

    session = create_session()
    session.headers.update(DEFAULT_HEADERS)

    issues: list[dict[str, Any]] = []
    fetch_errors = 0
    raw_offers: list[dict[str, Any]] = []

    # 1. Découverte des sous-pages modèles depuis les 8 hubs de marques
    model_links: list[tuple[str, str]] = []
    for b_slug in BRAND_HUB_SLUGS:
        hub_url = f"{REPARATIONS_URL}/{b_slug}"
        try:
            resp = session.get(hub_url, timeout=10)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                for a in soup.find_all("a"):
                    href = a.get("href", "")
                    if href.startswith(f"/reparations/{b_slug}/"):
                        full_u = f"https://prosmobile.fr{href}"
                        model_links.append((b_slug, full_u))
        except Exception as e:
            fetch_errors += 1
            issues.append(fetch_issue(hub_url, f"Erreur hub {b_slug}: {e}", REASON_FETCH_ERROR))

    distinct_links = sorted(list(set(model_links)))
    if max_models:
        distinct_links = distinct_links[:max_models]

    print(f"[{SOURCE_NAME}] {len(distinct_links)} pages modèles identifiées. Extraction en cours...", flush=True)

    with concurrent.futures.ThreadPoolExecutor(max_workers=15) as executor:
        futures = [executor.submit(extract_subpage, item, session) for item in distinct_links]
        for f in concurrent.futures.as_completed(futures):
            res = f.result()
            if res:
                raw_offers.extend(res)

    print(f"[{SOURCE_NAME}] {len(raw_offers)} tarifs extraits des sous-pages modèles spécifiques.", flush=True)

    # 2. Extraction de la grille interactive globale /tarifs (avec support complet des boutons/déclinaisons)
    try:
        resp_tarifs = session.get(BASE_PAGE_URL, timeout=12)
        if resp_tarifs.status_code == 200:
            soup_tarifs = BeautifulSoup(resp_tarifs.text, "html.parser")
            tarifs_count = 0
            articles = soup_tarifs.find_all("article")

            for art in articles:
                h3 = art.find("h3")
                h3_text = h3.get_text(strip=True) if h3 else ""

                # Filtrer les consoles et tablettes iPad (focus smartphones)
                if any(k in h3_text.upper() for k in ["PS4", "PS5", "SWITCH", "IPAD"]):
                    continue

                buttons = [b.get_text(strip=True) for b in art.find_all("button")]
                dls = art.find_all("dl")

                if buttons and len(buttons) == len(dls):
                    models_data = [format_model(h3_text, b) + (dl,) for b, dl in zip(buttons, dls)]
                else:
                    brand = detect_brand(h3_text)
                    models_data = [(brand, h3_text, dl) for dl in dls]

                for brand, mod_name, dl in models_data:
                    for row in dl.find_all("div", class_=lambda c: c and "items-baseline" in c):
                        span = row.find("span")
                        piece_name = span.get_text(strip=True) if span else ""
                        price_spans = [s.get_text(strip=True) for s in row.find_all("span") if "€" in s.get_text()]
                        if not price_spans:
                            continue
                        pm = re.search(r"(\d+)", price_spans[0])
                        if not pm:
                            continue
                        price = float(pm.group(1))
                        if price <= 0:
                            continue

                        # Vérification des query parameters spécifiques dans les boutons actifs
                        a = row.find("a")
                        href_piece = ""
                        if a and a.get("href"):
                            parsed = urllib.parse.urlparse(a.get("href"))
                            params = urllib.parse.parse_qs(parsed.query)
                            href_piece = params.get("piece", [""])[0]

                        cur_mod = mod_name
                        cur_brand = brand
                        if href_piece:
                            hp = href_piece.upper()
                            if "HONOR 70 LITE" in hp:
                                cur_mod, cur_brand = "Honor 70 Lite", "Honor"
                            elif "REALME GT 7T" in hp:
                                cur_mod, cur_brand = "Realme GT 7T", "Realme"
                            elif "ONE PLUS 9" in hp:
                                cur_mod, cur_brand = "OnePlus 9", "OnePlus"

                        norm_key = piece_name.lower().strip()
                        repair_type = REPAIR_NORMALIZATION_MAP.get(norm_key)
                        if not repair_type:
                            canon, matched = normalize_repair_type(piece_name)
                            repair_type = canon if matched and canon else piece_name

                        raw_offers.append({
                            "brand": cur_brand,
                            "model": cur_mod,
                            "item_name": f"{piece_name} {cur_mod}",
                            "repair_type_raw": piece_name,
                            "repair_type": repair_type,
                            "price": price,
                            "source_url": BASE_PAGE_URL,
                        })
                        tarifs_count += 1

            print(f"[{SOURCE_NAME}] {tarifs_count} tarifs extraits de la grille interactive /tarifs.", flush=True)
    except Exception as e:
        fetch_errors += 1
        issues.append(fetch_issue(BASE_PAGE_URL, f"Erreur /tarifs: {e}", REASON_FETCH_ERROR))

    # 3. Formatage standardisé des lignes E-commerce AI V2
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
            "price_raw": f"{int(item['price'])} €",
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

    print(f"\n[PROSMOBILE] Terminé avec succès : {len(deduped_rows)} tarifs exportés dans {out_dir}", flush=True)

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
    parser = argparse.ArgumentParser(description="ProsMobile Scraper (Strasbourg)")
    parser.add_argument("--output-dir", default=str(_SCRAPERS_ROOT / "outputs"), help="Dossier de sortie")
    parser.add_argument("--no-clean", action="store_true", help="Désactiver le nettoyage")
    parser.add_argument("--no-report", action="store_true", help="Désactiver le rapport d'exécution")
    parser.add_argument("--max-models", type=int, default=None, help="Nombre max de modèles")
    args = parser.parse_args()

    scrape(
        output_dir=args.output_dir,
        apply_cleaning=not args.no_clean,
        emit_run_report=not args.no_report,
        max_models=args.max_models,
    )


if __name__ == "__main__":
    main()
