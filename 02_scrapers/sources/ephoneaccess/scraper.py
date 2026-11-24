#!/usr/bin/env python3
"""
scraper_ephone_access.py
========================
Scraper haute performance pour Ephone Access Marseille (Av. de Mazargues, 13008).
Extrait l'ensemble des offres de vente de smartphones (Neufs & Reconditionnés)
selon le standard E-commerce AI V2 (Univers : product_sales, 23 colonnes standardisées).

Génère 5 fichiers d'artefacts dans ./outputs/ :
  1. db_ephoneaccess.json
  2. db_ephoneaccess.jsonl
  3. db_ephoneaccess.csv
  4. db_ephoneaccess_run.json
  5. db_ephoneaccess_issues.jsonl
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
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent

# Importer les outils de normalisation E-commerce AI Scraping
sys.path.insert(0, str(PROJECT_ROOT / "unit_scrapers"))
sys.path.insert(0, str(PROJECT_ROOT / "ecommerce_ai_scraping_scraper_V2-main"))

try:
    from common_export import export_standard_artifacts
except ImportError:
    export_standard_artifacts = None

# Constantes Boutique Ephone Access Marseille
SOURCE_SITE = "ephoneaccess.com"
BASE_URL = "https://www.ephoneaccess.com"
SHOP_NAME = "Ephone Access Mazargues Marseille"
SHOP_ADDRESS = "Avenue de Mazargues"
SHOP_CITY = "Marseille"
SHOP_POSTAL = "13008"

PRODUCT_SALE_KEYS = [
    "source_site", "source_url", "timestamp", "scrape_started_at", "data_type",
    "device_type", "device_family", "brand", "model", "model_normalized",
    "item_title", "storage_gb", "storage_raw", "ram_gb", "color", "condition",
    "product_id", "is_currently_sold", "price", "currency", "price_raw",
    "seller_name", "shipping_cost"
]

# Catégories de vente smartphones ciblées
SALES_CATEGORIES = [
    {
        "url": "https://www.ephoneaccess.com/76-iphone-reconditionne-comme-neuf-premium-garantie-6-mois-a-prix-bas",
        "default_condition": "refurbished",
        "default_brand": "Apple",
        "label": "iPhone Reconditionné comme neuf"
    },
    {
        "url": "https://www.ephoneaccess.com/1895-samsung-occasion-reconditionne-comme-neuf-premium-garantie-6-mois-repare-ou-remplace",
        "default_condition": "refurbished",
        "default_brand": "Samsung",
        "label": "Samsung Occasion / Reconditionné"
    },
    {
        "url": "https://www.ephoneaccess.com/63-iphoneneuf",
        "default_condition": "new",
        "default_brand": "Apple",
        "label": "iPhone Neuf officiel"
    },
    {
        "url": "https://www.ephoneaccess.com/87-telephones-samsung-neufs",
        "default_condition": "new",
        "default_brand": "Samsung",
        "label": "Téléphones Samsung Neufs"
    },
    {
        "url": "https://www.ephoneaccess.com/1859-konrow",
        "default_condition": "new",
        "default_brand": "Konrow",
        "label": "Konrow Smartphones"
    },
    {
        "url": "https://www.ephoneaccess.com/62-telephone-occasion-neuf",
        "default_condition": "refurbished",
        "default_brand": "Auto",
        "label": "Hub Téléphones Occasion & Neuf"
    }
]

# Référentiel des couleurs
COMMON_COLORS = [
    "noir sidéral", "noir sideral", "midnight green", "vert nuit", "bleu nuit",
    "gris sidéral", "gris sideral", "rose gold", "bleu pacifique", "bleu alpin",
    "noir", "blanc", "bleu", "rouge", "vert", "jaune", "mauve", "violet",
    "lavande", "brume", "rose", "gold", "or", "argent", "silver", "gris",
    "graphite", "lumière stellaire", "starlight", "minuit", "midnight", "titane naturel",
    "titane noir", "titane blanc", "titane bleu"
]
COLOR_PATTERN = re.compile(rf"\b({'|'.join(re.escape(c) for c in COMMON_COLORS)})\b", re.IGNORECASE)
STORAGE_PATTERN = re.compile(r"(\d+)\s*(?:gb|go|giga|g)\b", re.IGNORECASE)
PRICE_PATTERN = re.compile(r"(\d+[\d\s]*[,\.]\d{2})", re.IGNORECASE)


def now_iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def extract_storage(text: str) -> Tuple[Optional[int], Optional[str]]:
    """Extrait la capacité de stockage en Go et la chaîne brute."""
    m = STORAGE_PATTERN.search(text)
    if m:
        val = int(m.group(1))
        # Filtre de cohérence pour smartphones (4 Go à 1024 Go)
        if 4 <= val <= 1024:
            return val, f"{val}GB"
    return None, None


def extract_color(text: str) -> Optional[str]:
    """Extrait le coloris officiel mentionné dans le titre."""
    m = COLOR_PATTERN.search(text)
    if m:
        return m.group(1).capitalize()
    return None


def clean_brand_model(raw_title: str, default_brand: str, url: str) -> Tuple[str, str, str]:
    """Détermine la marque canonique, le modèle brut et le modèle normalisé."""
    title_lower = raw_title.lower()
    url_lower = url.lower()

    # Détection marque
    if "iphone" in title_lower or "apple" in title_lower or "iphone" in url_lower:
        brand = "Apple"
    elif "samsung" in title_lower or "galaxy" in title_lower or "samsung" in url_lower:
        brand = "Samsung"
    elif "konrow" in title_lower or "konrow" in url_lower:
        brand = "Konrow"
    elif "xiaomi" in title_lower or "redmi" in title_lower:
        brand = "Xiaomi"
    elif default_brand != "Auto":
        brand = default_brand
    else:
        brand = "Générique"

    # Nettoyage modèle
    model = raw_title
    # Supprimer les mentions parasites de vente
    model = re.sub(r"(?i)\b(apple|samsung|konrow|reconditionn[ée]|comme neuf|premium|garantie\s*\d*\s*mois|à prix bas|prix bas|neuf|new)\b", "", model)
    model = STORAGE_PATTERN.sub("", model)
    color = extract_color(raw_title)
    if color:
        model = re.sub(rf"(?i)\b{re.escape(color)}\b", "", model)
    model = re.sub(r"\s+", " ", model).strip(" -_,")

    # Résolution des modèles tronqués ou spécifiques
    if brand == "Apple":
        if re.search(r"\bmini\b", model, re.I) and not re.search(r"iphone\s*(12|13)", model, re.I):
            if "12" in url_lower:
                model = "iPhone 12 Mini"
            elif "13" in url_lower:
                model = "iPhone 13 Mini"
            else:
                model = "iPhone 12 Mini"
        if not model.lower().startswith("iphone"):
            model = f"iPhone {model}".strip()
        # Normalisation typographique
        model = re.sub(r"(?i)\biphone\b", "iPhone", model)
        model = re.sub(r"(?i)\bpro max\b", "Pro Max", model)
        model = re.sub(r"(?i)\bpro\b", "Pro", model)
        model = re.sub(r"(?i)\bplus\b", "Plus", model)
        model = re.sub(r"(?i)\bmini\b", "Mini", model)
        model = re.sub(r"(?i)\bse\s*2020\b", "SE (2020)", model)
        model = re.sub(r"(?i)\bse\s*2022\b", "SE (2022)", model)
    elif brand == "Samsung":
        if not model.lower().startswith("galaxy"):
            model = f"Galaxy {model}".strip()
        model = re.sub(r"(?i)\bgalaxy\b", "Galaxy", model)
        model = re.sub(r"(?i)\bultra\b", "Ultra", model)
        model = re.sub(r"(?i)\bfe\b", "FE", model)

    model = re.sub(r"\s+", " ", model).strip(" -_,")
    model_norm = model.lower()

    return brand, model, model_norm


class EphoneAccessScraper:
    """Scraper robuste pour les offres de vente smartphones Ephone Access Marseille."""

    def __init__(self, output_dir: Path, timeout: int = 15):
        self.output_dir = output_dir
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
            "Cache-Control": "no-cache",
        })

    def scrape_category(self, cat_info: dict[str, str]) -> List[Dict[str, Any]]:
        """Scrape une catégorie avec gestion de toutes les pages de pagination."""
        cat_url = cat_info["url"]
        default_condition = cat_info["default_condition"]
        default_brand = cat_info["default_brand"]
        label = cat_info["label"]

        offers: List[Dict[str, Any]] = []
        page = 1

        print(f"  📂 Scraping rayon : {label}...")

        while True:
            p_url = f"{cat_url}?p={page}" if page > 1 else cat_url
            try:
                resp = self.session.get(p_url, timeout=self.timeout)
                if resp.status_code != 200:
                    break

                soup = BeautifulSoup(resp.text, "html.parser")
                items = soup.select(".ajax_block_product, .product-container")
                if not items:
                    break

                found_new = 0
                for item in items:
                    # Extraction lien & titre
                    link_el = item.select_one("a.product_img_link, a.product-name")
                    title_el = item.select_one(".product-name")
                    price_el = item.select_one(".price.product-price, .price, [itemprop='price']")

                    if not link_el or not title_el or not price_el:
                        continue

                    raw_title = title_el.get_text(strip=True)
                    price_text = price_el.get_text(strip=True)
                    product_url = link_el.get("href") or ""
                    if product_url.startswith("/"):
                        product_url = urljoin(BASE_URL, product_url)

                    # Ignorer pièces détachées et forfaits de réparation qui pourraient s'égarer
                    if any(w in raw_title.lower() for w in [
                        "réparation", "reparation", "forfait", "vitre tactile", "bloc écran",
                        "chassis", "connecteur", "nappe", "camera arriere", "haut-parleur"
                    ]):
                        continue

                    # Parsing du prix
                    p_match = PRICE_PATTERN.search(price_text)
                    if not p_match:
                        continue
                    price_val = float(p_match.group(1).replace(" ", "").replace(",", "."))
                    if price_val < 25.0:  # Seuil minimal smartphone
                        continue

                    # Condition (Neuf vs Reconditionné)
                    if default_condition in ("new", "refurbished"):
                        condition = default_condition
                    elif "reconditionn" in raw_title.lower() or "occasion" in raw_title.lower():
                        condition = "refurbished"
                    elif "neuf" in raw_title.lower():
                        condition = "new"
                    else:
                        condition = "refurbished"

                    # Extraction mémoire, couleur, marque, modèle
                    storage_gb, storage_raw = extract_storage(raw_title)
                    color = extract_color(raw_title)
                    brand, model, model_norm = clean_brand_model(raw_title, default_brand, product_url)

                    # ID produit depuis l'URL (ex: /24499-iphone-12...)
                    prod_id_match = re.search(r"/(\d+)-", product_url)
                    prod_id = prod_id_match.group(1) if prod_id_match else None

                    now_str = now_iso_utc()
                    record = {
                        "source_site": SOURCE_SITE,
                        "source_url": product_url,
                        "timestamp": now_str,
                        "scrape_started_at": now_str,
                        "data_type": "product_sales",
                        "device_type": "smartphone",
                        "device_family": brand.lower(),
                        "brand": brand,
                        "model": model,
                        "model_normalized": model_norm,
                        "item_title": raw_title,
                        "storage_gb": storage_gb,
                        "storage_raw": storage_raw,
                        "ram_gb": None,
                        "color": color,
                        "condition": condition,
                        "product_id": prod_id,
                        "is_currently_sold": True,
                        "price": price_val,
                        "currency": "EUR",
                        "price_raw": price_text,
                        "seller_name": SHOP_NAME,
                        "shipping_cost": "0.0 € (Retrait atelier Mazargues / Envoi)",
                    }
                    offers.append(record)
                    found_new += 1

                if found_new == 0:
                    break

                # Vérifier présence page suivante
                next_page_link = soup.select_one(f"a[href*='p={page + 1}']")
                if not next_page_link:
                    break

                page += 1
                time.sleep(0.3)

            except Exception as e:
                print(f"    ⚠️ Erreur sur {p_url} : {e}")
                break

        print(f"    ✅ {len(offers)} offres collectées pour {label}")
        return offers

    def run(self) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Lance l'extraction complète de tous les rayons Ephone Access."""
        start_time = now_iso_utc()
        print("=" * 80)
        print("🚀 [E-commerce AI Scraping Scraper V2] Démarrage extraction Ephone Access Marseille (Vente)")
        print(f"📍 Boutique physique : {SHOP_NAME} ({SHOP_ADDRESS}, {SHOP_POSTAL} {SHOP_CITY})")
        print(f"🌐 Site web          : {BASE_URL}")
        print("=" * 80)

        all_offers: List[Dict[str, Any]] = []
        issues: List[Dict[str, Any]] = []
        seen_urls: set[str] = set()

        for cat_info in SALES_CATEGORIES:
            cat_offers = self.scrape_category(cat_info)
            for offer in cat_offers:
                url = offer["source_url"]
                if url not in seen_urls:
                    seen_urls.add(url)
                    all_offers.append(offer)

        end_time = now_iso_utc()

        # Contrôle qualité et détection des issues
        for off in all_offers:
            if not off.get("storage_gb"):
                issues.append({
                    "severity": "WARNING",
                    "issue_type": "MISSING_STORAGE",
                    "item_title": off["item_title"],
                    "url": off["source_url"],
                    "message": "Capacité de stockage non mentionnée explicitement dans le titre"
                })
            if off["price"] < 30.0:
                issues.append({
                    "severity": "CRITICAL",
                    "issue_type": "SUSPICIOUS_PRICE",
                    "item_title": off["item_title"],
                    "price": off["price"],
                    "message": "Prix anormalement bas pour un smartphone"
                })

        # Export standardisé des 5 artefacts
        self.output_dir.mkdir(parents=True, exist_ok=True)
        if export_standard_artifacts:
            export_standard_artifacts(
                source_name="ephoneaccess",
                domain="product_sales",
                rows=all_offers,
                output_dir=self.output_dir,
                issues=issues,
                scrape_started_at=start_time,
                scrape_finished_at=end_time
            )
        else:
            self._fallback_export(all_offers, issues, start_time, end_time)

        # Rapport de synthèse
        self._print_summary(all_offers, issues)

        return all_offers, issues

    def _fallback_export(
        self,
        rows: List[Dict[str, Any]],
        issues: List[Dict[str, Any]],
        start_time: str,
        end_time: str
    ) -> None:
        """Fallback d'export strict si common_export n'est pas chargé."""
        prefix = "db_ephoneaccess"
        # 1. JSON
        with open(self.output_dir / f"{prefix}.json", "w", encoding="utf-8") as f:
            json.dump(rows, f, indent=2, ensure_ascii=False)
        # 2. JSONL
        with open(self.output_dir / f"{prefix}.jsonl", "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        # 3. CSV
        with open(self.output_dir / f"{prefix}.csv", "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=PRODUCT_SALE_KEYS)
            writer.writeheader()
            writer.writerows(rows)
        # 4. RUN METRICS
        run_data = {
            "source_site": SOURCE_SITE,
            "data_type": "product_sales",
            "scrape_started_at": start_time,
            "scrape_finished_at": end_time,
            "total_records": len(rows),
            "total_issues": len(issues),
            "status": "SUCCESS" if rows else "EMPTY"
        }
        with open(self.output_dir / f"{prefix}_run.json", "w", encoding="utf-8") as f:
            json.dump(run_data, f, indent=2, ensure_ascii=False)
        # 5. ISSUES
        with open(self.output_dir / f"{prefix}_issues.jsonl", "w", encoding="utf-8") as f:
            for iss in issues:
                f.write(json.dumps(iss, ensure_ascii=False) + "\n")

    def _print_summary(self, offers: List[Dict[str, Any]], issues: List[Dict[str, Any]]) -> None:
        """Affiche un récapitulatif complet et esthétique de l'extraction."""
        new_count = sum(1 for o in offers if o["condition"] == "new")
        refurb_count = sum(1 for o in offers if o["condition"] == "refurbished")
        apple_count = sum(1 for o in offers if o["brand"] == "Apple")
        samsung_count = sum(1 for o in offers if o["brand"] == "Samsung")
        other_count = len(offers) - (apple_count + samsung_count)

        min_p = min((o["price"] for o in offers), default=0.0)
        avg_p = sum((o["price"] for o in offers)) / len(offers) if offers else 0.0
        max_p = max((o["price"] for o in offers), default=0.0)

        print("\n" + "=" * 80)
        print("📊 RÉCAPITULATIF DE L'EXTRACTION — EPHONE ACCESS MARSEILLE")
        print("=" * 80)
        print(f"  Total offres uniques extraites : {len(offers)}")
        print(f"  🟢 Offres Neuf (new)           : {new_count}")
        print(f"  🟡 Offres Reconditionné (refurb) : {refurb_count}")
        print(f"  🍎 Modèles Apple               : {apple_count}")
        print(f"  📱 Modèles Samsung             : {samsung_count}")
        print(f"  📦 Autres Marques              : {other_count}")
        print(f"  💶 Fourchette de Prix          : {min_p:.2f} € — {max_p:.2f} € (Moyenne : {avg_p:.2f} €)")
        print(f"  ⚠️ Anomalies / Warnings        : {len(issues)}")
        print("-" * 80)
        print(f"  📁 Fichiers générés dans       : {self.output_dir.resolve()}/")
        print("     - db_ephoneaccess.json")
        print("     - db_ephoneaccess.jsonl")
        print("     - db_ephoneaccess.csv")
        print("     - db_ephoneaccess_run.json")
        print("     - db_ephoneaccess_issues.jsonl")
        print("=" * 80 + "\n")


def main():
    parser = argparse.ArgumentParser(description="Scraper de prix de vente smartphones Ephone Access Marseille")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=CURRENT_DIR / "outputs",
        help="Répertoire de destination des 5 artefacts de sortie (défaut: ./outputs)"
    )
    parser.add_argument("--timeout", type=int, default=15, help="Timeout HTTP par requête en secondes")
    args = parser.parse_args()

    scraper = EphoneAccessScraper(output_dir=args.output_dir, timeout=args.timeout)
    offers, issues = scraper.run()
    sys.exit(0 if offers else 1)


if __name__ == "__main__":
    main()
