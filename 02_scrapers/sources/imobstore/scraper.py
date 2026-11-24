"""iMobStore Scraper (https://imobstore.fr).

Atelier de réparation smartphones, tablettes, consoles et ordinateurs :
- Adresse : 128 Boulevard Voltaire, 13821 La Penne-sur-Huveaune (Marseille Est)
- Téléphone : 04 22 91 44 88
- SIRET : 912 417 136 00013 (RCS Marseille)
- Garantie : 6 mois pièces et main-d'œuvre
- Délais : Réparation express en boutique sans rendez-vous

Stratégie d'extraction optimale (Stratégie 1 - API REST WordPress) :
    1. Interrogation de l'API REST WordPress : https://imobstore.fr/wp-json/wp/v2/pages/{id}
    2. Extraction des 9 pages spécialisées (iPhone, Samsung, Pixel, iPad, Tab, PS5, Xbox, Switch, Mac).
    3. Parsing des matrices de tarification JavaScript `var REP` et `var M`.
    4. Distinction avancée des qualités d'écran (Compatible, Soft OLED, OLED premium, Origine constructeur).
    5. Normalisation standard 22 colonnes E-commerce AI V2.
    6. Nettoyage et export certifié des 5 artefacts standard (.jsonl, .csv, .json, _run.json, _issues.jsonl).
"""

from __future__ import annotations

import argparse
import json
import re
import ssl
import sys
from pathlib import Path
from typing import Any
import urllib.request

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

SOURCE_NAME = "imobstore"
SOURCE_DOMAIN = "imobstore.fr"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)

SHOP_INFO = {
    "shop_name": "iMobStore",
    "address": "128 Boulevard Voltaire",
    "city": "La Penne-sur-Huveaune",
    "postal_code": "13821",
    "phone": "04 22 91 44 88",
    "delay": "Réparation express en boutique sans rendez-vous",
    "warranty": "Garantie 6 mois pièces et main-d'œuvre",
}

# Pages WP contenant les matrices de tarification interactive
TARGET_PAGES: list[dict[str, Any]] = [
    {
        "id": 83,
        "brand": "Apple",
        "device_family": "Smartphone",
        "url": "https://www.imobstore.fr/smartphones/apple/",
    },
    {
        "id": 94,
        "brand": "Samsung",
        "device_family": "Smartphone",
        "url": "https://www.imobstore.fr/smartphones/samsung/",
    },
    {
        "id": 95,
        "brand": "Google",
        "device_family": "Smartphone",
        "url": "https://www.imobstore.fr/smartphones/google-pixel/",
    },
    {
        "id": 115,
        "brand": "Apple",
        "device_family": "Tablette",
        "url": "https://www.imobstore.fr/tablettes/apple/",
    },
    {
        "id": 116,
        "brand": "Samsung",
        "device_family": "Tablette",
        "url": "https://www.imobstore.fr/tablettes/samsung/",
    },
    {
        "id": 107,
        "brand": "Sony PlayStation",
        "device_family": "Console",
        "url": "https://www.imobstore.fr/consoles/playstation/",
    },
    {
        "id": 108,
        "brand": "Microsoft Xbox",
        "device_family": "Console",
        "url": "https://www.imobstore.fr/consoles/xbox/",
    },
    {
        "id": 109,
        "brand": "Nintendo",
        "device_family": "Console",
        "url": "https://www.imobstore.fr/consoles/nintendo/",
    },
    {
        "id": 118,
        "brand": "Apple",
        "device_family": "Ordinateur",
        "url": "https://www.imobstore.fr/ordinateurs/apple/",
    },
]

# Mapping direct vers les catégories canoniques E-commerce AI V2
CANONICAL_REPAIR_MAP: dict[str, str] = {
    # Écrans
    "gen": "Ecran LCD",
    "soft": "Ecran LCD",
    "oled": "Ecran LCD",
    "ori": "Ecran LCD",
    "ecran": "Ecran LCD",
    "dalle": "Ecran LCD",
    # Batterie
    "bat": "Batterie",
    "batt": "Batterie",
    # Châssis / Vitre
    "back": "Façade arrière",
    # Connecteurs & Ports
    "chg": "Connecteur de charge",
    "usb": "Connecteur de charge",
    "hdmi": "Connecteur de charge",
    # Caméras
    "camf": "Caméra avant",
    "camr": "Caméra arrière",
    "cam": "Caméra arrière",
    # Maintenance & Diagnostic
    "oxy": "Désoxydation",
    "diag": "Diagnostic",
    # Autres
    "nett": "Autre réparation",
    "vent": "Autre réparation",
    "alim": "Autre réparation",
    "lect": "Autre réparation",
    "cart": "Autre réparation",
    "stock": "Autre réparation",
    "joy": "Autre réparation",
    "joytmr": "Autre réparation",
    "btn": "Autre réparation",
    "rail": "Autre réparation",
    "cm": "Autre réparation",
    "clav": "Autre réparation",
    "track": "Autre réparation",
}


def fetch_wp_page(page_id: int) -> dict[str, Any] | None:
    """Télécharge la page WordPress via l'API REST officielle."""
    url = f"https://imobstore.fr/wp-json/wp/v2/pages/{page_id}"
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=12) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        print(f"[{SOURCE_NAME.upper()}] ⚠️ Erreur lors du chargement de la page WP {page_id}: {exc}")
        return None


def parse_page_pricing(content_html: str, brand: str) -> tuple[dict[str, str], list[tuple[str, str, str, dict[str, float]]]]:
    """Extrait REP (types de réparation) et M (modèles et tarifs) depuis le script JavaScript."""
    rep_entries: dict[str, str] = {}
    m_entries: list[tuple[str, str, str, dict[str, float]]] = []

    # 1. Extraction de var REP
    rep_match = re.search(r'var REP\s*=\s*(\{[\s\S]*?\});\s*(?:var|/\*|function)', content_html)
    if rep_match:
        rep_text = rep_match.group(1)
        for k, v in re.findall(r'[\"\']?(\w+)[\"\']?\s*:\s*\{([^}]+)\}', rep_text):
            n_match = re.search(r'n\s*:\s*\"([^\"]+)\"', v)
            if n_match:
                rep_entries[k] = n_match.group(1)

    # Compléments usuels si absents
    rep_entries.setdefault("diag", "Diagnostic / Recherche de panne")
    rep_entries.setdefault("oxy", "Désoxydation")

    # 2. Extraction de var M
    m_match = re.search(r'var M\s*=\s*(\[[\s\S]*?\]);\s*(?:var|/\*|function)', content_html)
    if m_match:
        m_text = m_match.group(1)
        pattern = re.compile(r'\[\s*\"([^\"]+)\"\s*,\s*\"([^\"]+)\"\s*,\s*\"([^\"]+)\"\s*,\s*\{([^}]+)\}\s*\]')
        for slug, raw_name, cat, p_str in pattern.findall(m_text):
            name_clean = raw_name.replace('\\"', '"').replace('\\', '').strip()
            prices_dict: dict[str, float] = {}

            for pk, pv in re.findall(r'[\"\']?(\w+)[\"\']?\s*:\s*([^,\s}]+)', p_str):
                pk = pk.strip('\"\'')
                pv = pv.strip('\"\'')
                if pv == "devis":
                    continue
                try:
                    price_val = float(pv)
                    if price_val > 0:
                        prices_dict[pk] = price_val
                except (ValueError, TypeError):
                    continue

            if prices_dict:
                m_entries.append((slug, name_clean, cat, prices_dict))

    return rep_entries, m_entries


def create_repair_row(
    page_cfg: dict[str, Any],
    model_name: str,
    model_sku: str,
    repair_key: str,
    repair_label_raw: str,
    price: float,
    scraped_at: str,
) -> dict[str, Any]:
    """Construit une ligne de réparation 22 colonnes standard E-commerce AI V2."""
    brand = page_cfg["brand"]
    device_family = page_cfg["device_family"]
    source_url = page_cfg["url"]

    canonical_rep = CANONICAL_REPAIR_MAP.get(repair_key, "Autre réparation")
    price_raw_str = f"{int(price) if price.is_integer() else price:.2f} €"
    if brand.lower() in repair_label_raw.lower():
        item_name = f"{repair_label_raw} {model_name}".strip()
    else:
        item_name = f"{repair_label_raw} {brand} {model_name}".strip()

    return {
        "source_site": SOURCE_DOMAIN,
        "source_url": source_url,
        "source": source_url,
        "timestamp": scraped_at,
        "data_type": "repair_price",
        "device_family": device_family,
        "brand": brand,
        "model": model_name,
        "item_name": item_name,
        "repair_type_raw": repair_label_raw,
        "repair_type": canonical_rep,
        "price": price,
        "price_raw": price_raw_str,
        "currency": "EUR",
        "currency_symbol": "€",
        "shop_name": SHOP_INFO["shop_name"],
        "address": SHOP_INFO["address"],
        "city": SHOP_INFO["city"],
        "postal_code": SHOP_INFO["postal_code"],
        "delay": SHOP_INFO["delay"],
        "warranty": SHOP_INFO["warranty"],
        "model_sku": model_sku,
        "repair_category": "Réparation en atelier",
        "scraped_at": scraped_at,
    }


def scrape(
    output_dir: str | Path | None = None,
    apply_cleaning: bool = True,
    emit_run_report: bool = True,
    max_models: int | None = None,
) -> list[dict[str, Any]]:
    scrape_started_at = now_iso_utc()
    out_dir = Path(output_dir) if output_dir else (_SCRAPERS_ROOT / "outputs")
    out_dir.mkdir(parents=True, exist_ok=True)

    prefix = f"{SOURCE_NAME}_repair_price_{scrape_started_at}"
    jsonl_path = out_dir / f"{prefix}.jsonl"
    csv_path = out_dir / f"{prefix}.csv"
    json_path = out_dir / f"{prefix}.json"

    fetch_errors: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    all_rows: list[dict[str, Any]] = []

    print(f"\n[{SOURCE_NAME.upper()}] 🌐 Démarrage du scraper iMobStore (La Penne-sur-Huveaune / Marseille Est)", flush=True)
    print(f"[{SOURCE_NAME.upper()}] 📍 Extraction de {len(TARGET_PAGES)} univers de tarification via WP REST API...", flush=True)

    total_models_processed = 0

    for page_cfg in TARGET_PAGES:
        pid = page_cfg["id"]
        brand = page_cfg["brand"]
        family = page_cfg["device_family"]

        data = fetch_wp_page(pid)
        if not data:
            fetch_errors.append(fetch_issue(page_cfg["url"], REASON_FETCH_ERROR, f"Échec requête WP page {pid}"))
            continue

        content = data.get("content", {}).get("rendered", "")
        rep_entries, m_entries = parse_page_pricing(content, brand)

        print(f"[{SOURCE_NAME.upper()}] 📱 Page {pid} ({brand} {family}) : {len(m_entries)} modèles tarifés identifiés", flush=True)

        for slug, model_name, cat, prices in m_entries:
            if max_models and total_models_processed >= max_models:
                break
            total_models_processed += 1

            for rep_key, price_val in prices.items():
                raw_label = rep_entries.get(rep_key, rep_key.replace("-", " ").capitalize())
                row = create_repair_row(
                    page_cfg=page_cfg,
                    model_name=model_name,
                    model_sku=slug,
                    repair_key=rep_key,
                    repair_label_raw=raw_label,
                    price=price_val,
                    scraped_at=scrape_started_at,
                )
                all_rows.append(row)

    print(f"[{SOURCE_NAME.upper()}] 🔄 Données brutes : {len(all_rows)} tarifs sur {total_models_processed} modèles", flush=True)

    # Déduplication
    deduped_rows = dedupe_dict_rows(
        all_rows,
        keys=["model", "repair_type_raw", "price", "shop_name"],
    )

    # Nettoyage E-commerce AI V2
    clean_report = None
    if apply_cleaning:
        cleaning_cfg = CleaningConfig(
            price_min=5.0,
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

    # Exportation des artefacts E-commerce AI V2
    write_jsonl_and_csv(deduped_rows, jsonl_path=jsonl_path, csv_path=csv_path)

    payload = {
        "source_site": SOURCE_NAME,
        "scrape_started_at": scrape_started_at,
        "row_count": len(deduped_rows),
        "rows": deduped_rows,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n[{SOURCE_NAME.upper()}] ✅ Succès : {len(deduped_rows)} tarifs exportés dans {out_dir}", flush=True)

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
    parser = argparse.ArgumentParser(description="iMobStore Scraper (La Penne-sur-Huveaune)")
    parser.add_argument("--output-dir", default=str(_SCRAPERS_ROOT / "outputs"), help="Dossier de sortie")
    parser.add_argument("--no-clean", action="store_true", help="Désactiver le nettoyage")
    parser.add_argument("--no-report", action="store_true", help="Désactiver le rapport d'exécution")
    parser.add_argument("--max-models", type=int, default=None, help="Nombre max de modèles extraits")
    args = parser.parse_args()

    scrape(
        output_dir=args.output_dir,
        apply_cleaning=not args.no_clean,
        emit_run_report=not args.no_report,
        max_models=args.max_models,
    )


if __name__ == "__main__":
    main()
