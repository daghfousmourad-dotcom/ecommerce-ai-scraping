#!/usr/bin/env python3
"""
scraper_pixou_phone.py
======================
Scraper haute performance pour Pixou Phone Marseille (171 Cours Lieutaud, 13006).
Interroge directement l'API JSON WordPress REST/AJAX du simulateur de devis en ligne.

Conforme aux standards E-commerce AI V2 :
- Univers : repair_and_parts (22 colonnes standardisées)
- 5 fichiers d'artefacts générés dans ./outputs/ :
  1. db_pixou_phone.json
  2. db_pixou_phone.jsonl
  3. db_pixou_phone.csv
  4. db_pixou_phone_run.json
  5. db_pixou_phone_issues.jsonl
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
import csv
import json
import os
import re
import sys
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent

# Importer les outils de normalisation du projet
sys.path.insert(0, str(PROJECT_ROOT / "unit_scrapers"))
sys.path.insert(0, str(PROJECT_ROOT / "ecommerce_ai_scraping_scraper_V2-main"))

try:
    from common_export import export_standard_artifacts
except ImportError:
    # Fallback autonome si non trouvé
    export_standard_artifacts = None

# Constantes atelier Marseille
SOURCE_SITE = "pixouphone.fr"
SOURCE_URL = "https://pixouphone.fr/devis-en-ligne/"
AJAX_URL = "https://pixouphone.fr/wp-admin/admin-ajax.php"
SHOP_NAME = "Pixou Phone Marseille"
SHOP_ADDRESS = "171 Cours Lieutaud"
SHOP_CITY = "Marseille"
SHOP_POSTAL = "13006"
DEFAULT_DELAY = "30 minutes"
DEFAULT_WARRANTY = "Garantie 6 mois"

REPAIR_KEYS = [
    "source_site", "source_url", "timestamp", "scrape_started_at", "data_type",
    "device_type", "device_family", "brand", "model", "model_normalized",
    "product_or_part_name", "repair_type_raw", "repair_type", "repair_type_matched",
    "price", "currency", "price_raw", "warranty", "delay",
    "shop_name", "city", "postal_code"
]

# Mapping canonique expert pour Pixou Phone
TAXONOMY_MAP: dict[str, str] = {
    "ecran": "Écran",
    "écran": "Écran",
    "batterie": "Batterie",
    "connecteur de charge": "Connecteur de charge",
    "charge": "Connecteur de charge",
    "vitre arriere": "Vitre arrière",
    "vitre arrière": "Vitre arrière",
    "facade arriere": "Vitre arrière",
    "façade arrière": "Vitre arrière",
    "dos": "Vitre arrière",
    "camera frontale": "Appareil photo avant",
    "caméra frontale": "Appareil photo avant",
    "camera arriere": "Appareil photo arrière",
    "caméra arrière": "Appareil photo arrière",
    "camèra arrière": "Appareil photo arrière",
    "lentille camera": "Lentille caméra",
    "lentille caméra": "Lentille caméra",
    "lentille camera arriere": "Lentille caméra",
    "lentille caméra arrière": "Lentille caméra",
    "haut-parleur (haut)": "Haut-parleur interne (écouteur)",
    "haut-parleur (bas)": "Haut-parleur externe",
    "haut parleur": "Haut-parleur externe",
    "ecouteur": "Haut-parleur interne (écouteur)",
    "écouteur": "Haut-parleur interne (écouteur)",
    "vibreur": "Vibreur",
    "micro": "Microphone",
    "desoxydation": "Désoxydation",
    "désoxydation": "Désoxydation",
    "diagnostic": "Diagnostic",
    "reinitialisation": "Réinitialisation logicielle",
    "réinitialisation": "Réinitialisation logicielle",
    "transfert de donnees": "Transfert de données",
    "transfert de données": "Transfert de données",
    "erreur logiciel": "Réparation logicielle",
    "carte mere": "Micro-soudure carte mère",
    "carte mère": "Micro-soudure carte mère",
    "capteur de proximite": "Capteur de proximité",
    "capteur de proximité": "Capteur de proximité",
    "bouton power": "Bouton Power / Allumage",
    "bouton(s) de volume": "Boutons de volume",
    "bouton volume": "Boutons de volume",
    "bouton acceuil": "Bouton Home / Accueil",
    "face id": "Réparation Face ID",
    "touch id": "Réparation Touch ID",
    "nappe induction": "Nappe charge sans fil induction",
}


def now_iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_model_name(name: str) -> str:
    if not name:
        return ""
    text = unicodedata.normalize("NFKD", name.strip().lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-z0-9]+", " ", text).strip()
    return text


def map_repair_type(raw_name: str) -> Tuple[str, bool]:
    """Mappe le nom brut de réparation vers la taxonomie officielle E-commerce AI Scraping."""
    if not raw_name:
        return "Autres pannes", False

    clean = unicodedata.normalize("NFKD", raw_name.strip().lower())
    clean = "".join(c for c in clean if not unicodedata.combining(c))

    # 1. Correspondance directe
    for pattern, canon in TAXONOMY_MAP.items():
        pat_clean = unicodedata.normalize("NFKD", pattern)
        pat_clean = "".join(c for c in pat_clean if not unicodedata.combining(c))
        if pat_clean in clean:
            return canon, True

    # 2. Nettoyage et repli
    titled = raw_name.strip().title()
    return titled, False


def extract_warranty(desc: str) -> str:
    """Extrait la garantie mentionnée dans la description de la formule."""
    if not desc:
        return DEFAULT_WARRANTY
    m = re.search(r"garantie\s+(\d+\s*(?:an[s]?|mois|semaine[s]?))", desc, re.I)
    if m:
        return f"Garantie {m.group(1)}"
    if "garantie à vie" in desc.lower():
        return "Garantie à vie"
    return DEFAULT_WARRANTY


class PixouPhoneScraper:
    def __init__(self, user_agent: str = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": user_agent,
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": "https://pixouphone.fr",
            "Referer": SOURCE_URL,
        })
        self.issues: List[Dict[str, Any]] = []

    def get_brands(self, category: str = "Smartphone") -> List[Dict[str, Any]]:
        """Récupère la liste des marques pour une catégorie donnée."""
        try:
            resp = self.session.post(
                AJAX_URL,
                data={"action": "rp_fe_get_brands", "c_name": category},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("status"):
                return data.get("results", [])
        except Exception as e:
            self.issues.append({
                "type": "FETCH_ERROR_BRANDS",
                "category": category,
                "error": str(e),
                "timestamp": now_iso_utc(),
            })
        return []

    def get_models(self, category: str, brand_name: str) -> List[Dict[str, Any]]:
        """Récupère tous les modèles pour une marque donnée."""
        try:
            resp = self.session.post(
                AJAX_URL,
                data={"action": "rp_fe_get_models", "c_name": category, "b_name": brand_name},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("status"):
                raw_models = data.get("results", [])
                models_keys = data.get("models_keys", {"A": "m_id", "B": "m_name"})
                name_key = "B" if "B" in models_keys else "m_name"
                id_key = "A" if "A" in models_keys else "m_id"

                parsed_models = []
                for m in raw_models:
                    m_name = m.get(name_key) or m.get("m_name")
                    m_id = m.get(id_key) or m.get("m_id")
                    if m_name:
                        parsed_models.append({"m_id": m_id, "m_name": m_name.strip()})
                return parsed_models
        except Exception as e:
            self.issues.append({
                "type": "FETCH_ERROR_MODELS",
                "brand": brand_name,
                "error": str(e),
                "timestamp": now_iso_utc(),
            })
        return []

    def get_model_repairs(self, brand_name: str, model_name: str) -> List[Dict[str, Any]]:
        """Récupère l'intégralité des forfaits et tarifs de réparation pour un modèle."""
        try:
            resp = self.session.post(
                AJAX_URL,
                data={"action": "rp_fe_get_repairs", "b_name": brand_name, "m_name": model_name},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            if not data.get("status"):
                return []

            limited = data.get("limited_repairs", [])
            remaining = data.get("remaining_repairs", [])
            all_repairs = limited + remaining
            repair_attrs = data.get("repair_attrs_from_db", {})

            offers = []
            now_str = now_iso_utc()

            for rep in all_repairs:
                r_id = str(rep.get("r_id", ""))
                r_name = rep.get("r_name", "").strip()
                r_duration = rep.get("r_duration", "30")
                r_duration_measure = rep.get("r_duration_measure", "minutes")
                delay_str = f"{r_duration} {r_duration_measure}".strip()

                canon_repair, is_matched = map_repair_type(r_name)
                tiers = repair_attrs.get(r_id, [])

                # Cas 1 : La réparation propose plusieurs niveaux de qualité (ex: Écran OLED vs LCD, Batterie OEM vs Compatible)
                if tiers:
                    for t in tiers:
                        tier_name = t.get("B", "").strip()
                        tier_desc = t.get("C", "").strip()
                        tier_price_raw = t.get("D", "0")
                        tier_duration = t.get("H") or r_duration
                        tier_delay_str = f"{tier_duration} {r_duration_measure}".strip()

                        try:
                            price_val = float(str(tier_price_raw).replace(",", "."))
                        except (ValueError, TypeError):
                            price_val = 0.0

                        if price_val <= 0:
                            continue

                        warranty_str = extract_warranty(tier_desc)
                        product_name = f"{r_name} ({tier_name})" if tier_name else r_name

                        offers.append({
                            "source_site": SOURCE_SITE,
                            "source_url": SOURCE_URL,
                            "timestamp": now_str,
                            "scrape_started_at": now_str,
                            "data_type": "repair_price",
                            "device_type": "smartphone",
                            "device_family": brand_name,
                            "brand": brand_name,
                            "model": model_name,
                            "model_normalized": normalize_model_name(model_name),
                            "product_or_part_name": product_name,
                            "repair_type_raw": r_name,
                            "repair_type": canon_repair,
                            "repair_type_matched": is_matched,
                            "price": round(price_val, 2),
                            "currency": "EUR",
                            "price_raw": f"{price_val:.2f} €",
                            "warranty": warranty_str,
                            "delay": tier_delay_str,
                            "shop_name": SHOP_NAME,
                            "city": SHOP_CITY,
                            "postal_code": SHOP_POSTAL,
                        })

                # Cas 2 : Réparation à tarif fixe direct
                else:
                    direct_price_raw = rep.get("r_price", 0)
                    r_desc = rep.get("r_discription", "")
                    try:
                        price_val = float(str(direct_price_raw).replace(",", "."))
                    except (ValueError, TypeError):
                        price_val = 0.0

                    if price_val <= 0:
                        continue

                    warranty_str = extract_warranty(r_desc)

                    offers.append({
                        "source_site": SOURCE_SITE,
                        "source_url": SOURCE_URL,
                        "timestamp": now_str,
                        "scrape_started_at": now_str,
                        "data_type": "repair_price",
                        "device_type": "smartphone",
                        "device_family": brand_name,
                        "brand": brand_name,
                        "model": model_name,
                        "model_normalized": normalize_model_name(model_name),
                        "product_or_part_name": r_name,
                        "repair_type_raw": r_name,
                        "repair_type": canon_repair,
                        "repair_type_matched": is_matched,
                        "price": round(price_val, 2),
                        "currency": "EUR",
                        "price_raw": f"{price_val:.2f} €",
                        "warranty": warranty_str,
                        "delay": delay_str,
                        "shop_name": SHOP_NAME,
                        "city": SHOP_CITY,
                        "postal_code": SHOP_POSTAL,
                    })

            return offers

        except Exception as e:
            self.issues.append({
                "type": "FETCH_ERROR_REPAIRS",
                "brand": brand_name,
                "model": model_name,
                "error": str(e),
                "timestamp": now_iso_utc(),
            })
            return []


def export_artifacts(
    rows: List[Dict[str, Any]],
    issues: List[Dict[str, Any]],
    output_dir: Path,
    started_at: str,
    finished_at: str,
) -> Dict[str, str]:
    """Exporte rigoureusement les 5 fichiers d'artefacts E-commerce AI V2."""
    output_dir.mkdir(parents=True, exist_ok=True)
    source_name = "pixou_phone"
    prefix = f"db_{source_name}"

    json_path = output_dir / f"{prefix}.json"
    jsonl_path = output_dir / f"{prefix}.jsonl"
    csv_path = output_dir / f"{prefix}.csv"
    run_path = output_dir / f"{prefix}_run.json"
    issues_path = output_dir / f"{prefix}_issues.jsonl"

    # 1. JSON
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, ensure_ascii=False)

    # 2. JSONL
    with open(jsonl_path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # 3. CSV (avec extrasaction='ignore' pour conformité absolue)
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=REPAIR_KEYS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    # 4. ISSUES JSONL
    with open(issues_path, "w", encoding="utf-8") as f:
        for iss in issues:
            f.write(json.dumps(iss, ensure_ascii=False) + "\n")

    # 5. RUN JSON
    run_meta = {
        "source_site": SOURCE_SITE,
        "shop_name": SHOP_NAME,
        "address": f"{SHOP_ADDRESS}, {SHOP_POSTAL} {SHOP_CITY}",
        "scrape_started_at": started_at,
        "scrape_finished_at": finished_at,
        "schema_version": 1,
        "status": "completed",
        "row_count": len(rows),
        "paths": {
            "json": str(json_path),
            "jsonl": str(jsonl_path),
            "csv": str(csv_path),
            "run_json": str(run_path),
            "issues_jsonl": str(issues_path),
        },
        "counts": {
            "fetch_errors": len(issues),
            "issues_total": len(issues),
        },
    }
    with open(run_path, "w", encoding="utf-8") as f:
        json.dump(run_meta, f, indent=2, ensure_ascii=False)

    return {
        "json": str(json_path),
        "jsonl": str(jsonl_path),
        "csv": str(csv_path),
        "run_json": str(run_path),
        "issues_jsonl": str(issues_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Scraper Pixou Phone Marseille (API devis)")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=CURRENT_DIR / "outputs",
        help="Dossier de sortie des artefacts (défaut: ./liste_des_web/outputs)",
    )
    parser.add_argument(
        "--brands",
        type=str,
        default="Apple,Samsung,Xiaomi,Google",
        help="Marques à extraire séparées par des virgules (ou 'all')",
    )
    parser.add_argument(
        "--limit-models",
        type=int,
        default=None,
        help="Nombre maximal de modèles par marque pour tester rapidement",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=6,
        help="Nombre de requêtes parallèles concurrentes (défaut: 6)",
    )
    args = parser.parse_args()

    started_at = now_iso_utc()
    print("=" * 70)
    print(f"🚀 [Pixou Phone Marseille] Démarrage du scraping : {SHOP_NAME}")
    print(f"📍 Adresse : {SHOP_ADDRESS}, {SHOP_POSTAL} {SHOP_CITY}")
    print(f"📁 Dossier de sortie : {args.output_dir}")
    print("=" * 70)

    scraper = PixouPhoneScraper()

    # 1. Obtenir les marques
    all_brands_raw = scraper.get_brands(category="Smartphone")
    all_brand_names = [b.get("b_name") for b in all_brands_raw if b.get("b_name")]

    if args.brands.lower() == "all":
        target_brands = all_brand_names
    else:
        requested = [b.strip().lower() for b in args.brands.split(",")]
        target_brands = [b for b in all_brand_names if b.lower() in requested]

    print(f"🏷️  Marques cibles ({len(target_brands)}) : {', '.join(target_brands)}")

    all_models_to_scrape: List[Tuple[str, str]] = []
    for brand in target_brands:
        models = scraper.get_models("Smartphone", brand)
        if args.limit_models:
            models = models[:args.limit_models]
        print(f"   ↳ {brand:<10} : {len(models)} modèles identifiés")
        for m in models:
            all_models_to_scrape.append((brand, m["m_name"]))

    print(f"\n📦 Total de modèles à scraper : {len(all_models_to_scrape)}")
    print(f"⚡ Extraction parallèle (concurrence = {args.concurrency})...\n")

    all_rows: List[Dict[str, Any]] = []
    completed_count = 0

    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = {
            executor.submit(scraper.get_model_repairs, brand, model): (brand, model)
            for brand, model in all_models_to_scrape
        }

        for future in as_completed(futures):
            brand, model = futures[future]
            completed_count += 1
            try:
                offers = future.result()
                all_rows.extend(offers)
                if completed_count % 10 == 0 or completed_count == len(all_models_to_scrape):
                    pct = (completed_count / len(all_models_to_scrape)) * 100
                    print(f"   [{completed_count:>3}/{len(all_models_to_scrape)}] ({pct:5.1f}%) Traité : {brand} {model:<25} -> {len(offers)} offres")
            except Exception as e:
                scraper.issues.append({
                    "type": "THREAD_ERROR",
                    "brand": brand,
                    "model": model,
                    "error": str(e),
                    "timestamp": now_iso_utc(),
                })

    finished_at = now_iso_utc()

    # 4. Export des 5 fichiers standards
    print("\n" + "=" * 70)
    print(f"💾 Export des 5 artefacts standardisés dans {args.output_dir}...")
    paths = export_artifacts(
        rows=all_rows,
        issues=scraper.issues,
        output_dir=args.output_dir,
        started_at=started_at,
        finished_at=finished_at,
    )

    print("✅ Extraction terminée avec succès !")
    print(f"📊 Total offres de réparation extraites : {len(all_rows)}")
    print(f"⚠️  Total anomalies / issues : {len(scraper.issues)}")
    for k, p in paths.items():
        print(f"   📄 {k:<12} : {p}")
    print("=" * 70)


if __name__ == "__main__":
    main()
