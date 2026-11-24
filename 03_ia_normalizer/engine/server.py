"""
Serveur Web Dashboard Asynchrone (aiohttp) pour le Système IA de Normalisation E-commerce AI V2.
"""

from __future__ import annotations

import asyncio
import csv
import datetime
import glob
import json
import os
import shutil
import sys
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from aiohttp import web

# Configuration et modules E-commerce AI V2
CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent

sys.path.insert(0, str(CURRENT_DIR))
sys.path.insert(0, str(PROJECT_ROOT / "03_ia_normalizer" / "engine"))
sys.path.insert(0, str(PROJECT_ROOT / "02_scrapers"))

try:
    from .config import (
        AVAILABLE_MODELS,
        BASE_DIR,
        DASHBOARD_HOST,
        DASHBOARD_PORT,
        DEFAULT_BATCH_SIZE,
        DEFAULT_CONCURRENCY,
        DEFAULT_MODEL,
        INPUT_DATASET_DIR,
        INPUT_OUTPUTS_DIR,
        LISTE_DES_WEB_OUTPUTS_DIR,
        MERGE_SCRIPT_PATH,
        OUTPUT_AI_DIR,
        UNIFIED_DB_PATH,
        UNIFIED_INDEX_PATH,
        UNITED_DATASET_DIR,
    )
except (ImportError, ValueError):
    from config import (
        AVAILABLE_MODELS,
        BASE_DIR,
        DASHBOARD_HOST,
        DASHBOARD_PORT,
        DEFAULT_BATCH_SIZE,
        DEFAULT_CONCURRENCY,
        DEFAULT_MODEL,
        INPUT_DATASET_DIR,
        INPUT_OUTPUTS_DIR,
        LISTE_DES_WEB_OUTPUTS_DIR,
        MERGE_SCRIPT_PATH,
        OUTPUT_AI_DIR,
        UNIFIED_DB_PATH,
        UNIFIED_INDEX_PATH,
        UNITED_DATASET_DIR,
    )

try:
    from engine import AsyncAINormalizer
except Exception as e:
    print(f"⚠️ Warning : Moteur IA normalizer en mode dégradé : {e}")
    AsyncAINormalizer = None

# Mapping officiel des sources E-commerce AI V2 (enrichi avec les nouveaux scrapers qualifiés)
SOURCES_CONFIG = [
    {
        "source": "save",
        "name": "Save.co",
        "domain": "Réparation",
        "icon": "🔧",
        "prefix": "save_repair_prices",
        "ds_name": "db_save",
        "ds_v": "db_scraperV1",
    },
    {
        "source": "phone_cbd",
        "name": "Phone & CBD",
        "domain": "Réparation",
        "icon": "📱",
        "prefix": "phone_cbd_repair_prices",
        "ds_name": "db_phone_cbd",
        "ds_v": "db_scraperV2",
    },
    {
        "source": "apple",
        "name": "Apple Officiel (Vente)",
        "domain": "Vente Neuf & Reconditionné",
        "icon": "🍎",
        "prefix": "apple_official_prices_20260919",
        "ds_name": "db_apple",
        "ds_v": "db_scraperV3",
    },
    {
        "source": "apple_repair",
        "name": "Apple Réparation iPhone",
        "domain": "Réparations Officielles",
        "icon": "🍎",
        "prefix": "apple_repair_prices",
        "ds_name": "db_apple_repair",
        "ds_v": "db_apple_repair",
    },
    {
        "source": "samsung",
        "name": "Samsung Officiel (Vente)",
        "domain": "Vente Neuf & Reconditionné",
        "icon": "🪐",
        "prefix": "samsung_official_prices_20260919",
        "ds_name": "db_samsung",
        "ds_v": "db_scraperV4",
    },
    {
        "source": "samsung_repair",
        "name": "Samsung Smart Repair",
        "domain": "Réparations Officielles",
        "icon": "🪐",
        "prefix": "samsung_repair_prices",
        "ds_name": "db_samsung_repair",
        "ds_v": "db_samsung_repair",
    },
    {
        "source": "docteur_it",
        "name": "Docteur IT",
        "domain": "Réseau Réparation (National)",
        "icon": "🩺",
        "prefix": "docteur_it_repair_prices",
        "ds_name": "db_docteur_it",
        "ds_v": "db_docteur_it",
    },
    {
        "source": "iriparo",
        "name": "iRiparo France",
        "domain": "Réseau Réparation (35+ Marques)",
        "icon": "🔧",
        "prefix": "iriparo_repair_prices",
        "ds_name": "db_iriparo",
        "ds_v": "db_iriparo",
    },
    {
        "source": "xiaomi",
        "name": "Xiaomi France – Pièces",
        "domain": "Pièces & Réparations Officielles",
        "icon": "📱",
        "prefix": "xiaomi_official_prices",
        "ds_name": "db_xiaomi",
        "ds_v": "db_xiaomi",
    },
    # NOTE: Sources suivantes auditées et écartées (5 sources) — non scrapables ou sans grille tarifaire :
    # - Mistral Phone Marseille : 0 prix en ligne, devis uniquement
    # - Stuff Telecom Marseille : boutique accessoires, 0 grille réparation
    # - SmartphonePro Marseille : devis uniquement, pas de tarif public (WAF 403)
    # - i-Rep Marseille        : devis uniquement (pas de grille publique)
    # - Mister Minit           : devis en boutique uniquement (aucun tarif en ligne)
    {
        "source": "pixel_store",
        "name": "Pixel Store France",
        "domain": "Réparations Multi-Marques",
        "icon": "🏪",
        "prefix": "pixel_store_repair_prices",
        "ds_name": "db_pixel_store",
        "ds_v": "db_pixel_store",
    },
    {
        "source": "phonethik",
        "name": "Phon'Ethik",
        "domain": "Réparations Multi-Marques",
        "icon": "🌱",
        "prefix": "phonethik_repair_prices",
        "ds_name": "db_phonethik",
        "ds_v": "db_phonethik",
    },
    {
        "source": "oppo",
        "name": "OPPO France – Pièces",
        "domain": "Pièces & Réparations Officielles",
        "icon": "🟢",
        "prefix": "oppo_repair_prices",
        "ds_name": "db_oppo",
        "ds_v": "db_oppo",
    },
    {
        "source": "honor",
        "name": "HONOR France – Pièces",
        "domain": "Pièces & Réparations Officielles",
        "icon": "🔵",
        "prefix": "honor_repair_prices",
        "ds_name": "db_honor",
        "ds_v": "db_honor",
    },
    {
        "source": "google",
        "name": "Google Pixel Repair",
        "domain": "Réparations Officielles Google",
        "icon": "🔴",
        "prefix": "google_repair_prices",
        "ds_name": "db_google",
        "ds_v": "db_google",
    },
    {
        "source": "phone_express",
        "name": "Phone Express",
        "domain": "Grille Réparation Express",
        "icon": "⏱️",
        "prefix": "phone_express_repair_prices",
        "ds_name": "db_phone_express",
        "ds_v": "db_phone_express",
    },
    {
        "source": "corner_mobile",
        "name": "Corner Mobile",
        "domain": "Ateliers Réparation",
        "icon": "📍",
        "prefix": "corner_mobile_repair_prices",
        "ds_name": "db_corner_mobile",
        "ds_v": "db_corner_mobile",
    },
    {
        "source": "smart_repair_63",
        "name": "Smart Repair 63",
        "domain": "Ateliers Réparation",
        "icon": "🛠️",
        "prefix": "smart_repair_63_repair_prices",
        "ds_name": "db_smart_repair_63",
        "ds_v": "db_smart_repair_63",
    },
    {
        "source": "ebay",
        "name": "eBay France",
        "domain": "Vente Occasion",
        "icon": "🏷️",
        "prefix": "ebay_market_prices",
        "ds_name": "db_ebay",
        "ds_v": "db_scraperV5",
    },
    {
        "source": "wefix",
        "name": "WeFix Fnac",
        "domain": "Réparation",
        "icon": "🛠️",
        "prefix": "wefix_repair_prices",
        "ds_name": "db_wefix",
        "ds_v": "db_scraperV6",
    },
    {
        "source": "utopya",
        "name": "Utopya Grossiste B2B",
        "domain": "Pièces Détachées",
        "icon": "📦",
        "prefix": "utopya_part_costs",
        "ds_name": "db_utopya",
        "ds_v": "db_scraperV7",
    },
    {
        "source": "amazon",
        "name": "Amazon France",
        "domain": "Vente Occasion",
        "icon": "🛒",
        "prefix": "amazon_market_prices",
        "ds_name": "db_amazon",
        "ds_v": "db_scraperV8",
    },
    {
        "source": "darty",
        "name": "Darty Marketplace",
        "domain": "Vente Occasion",
        "icon": "🏪",
        "prefix": "darty_smartphones",
        "ds_name": "db_darty",
        "ds_v": "db_scraperV9",
    },
    {
        "source": "targets",
        "name": "Targets Marseille",
        "domain": "Ateliers Réparation",
        "icon": "🎯",
        "prefix": "targets_prices",
        "ds_name": "db_targets",
        "ds_v": "db_scraperV10",
    },
    {
        "source": "pixou_phone",
        "name": "Pixou Phone Marseille",
        "domain": "Réparations Ateliers",
        "icon": "🔧",
        "prefix": "db_pixou_phone",
        "ds_name": "db_pixou_phone",
        "ds_v": "db_pixou_phone",
    },
    {
        "source": "ephoneaccess",
        "name": "Ephone Access Marseille",
        "domain": "Vente & Réparations (3-en-1)",
        "icon": "⚡",
        "prefix": "db_ephoneaccess",
        "ds_name": "db_ephoneaccess",
        "ds_v": "db_ephoneaccess",
    },
    {
        "source": "leboncoin",
        "name": "Leboncoin France",
        "domain": "Vente Occasion C2C/B2C",
        "icon": "🛍️",
        "prefix": "db_leboncoin",
        "ds_name": "db_leboncoin",
        "ds_v": "db_leboncoin",
    },
    {
        "source": "ldlc",
        "name": "LDLC Marseille & National",
        "domain": "Vente Neuf & Reconditionné",
        "icon": "🛒",
        "prefix": "db_ldlc",
        "ds_name": "db_ldlc",
        "ds_v": "db_ldlc",
    },
    {
        "source": "prosmobile",
        "name": "ProsMobile Strasbourg",
        "domain": "Réparations Multi-Marques",
        "icon": "🔧",
        "prefix": "prosmobile_repair_prices",
        "ds_name": "db_prosmobile",
        "ds_v": "db_prosmobile",
    },
    {
        "source": "mon_reparateur_pro",
        "name": "Mon Réparateur Pro Clermont-Ferrand",
        "domain": "Réparations Multi-Marques",
        "icon": "⚡",
        "prefix": "mon_reparateur_pro_repair_prices",
        "ds_name": "db_mon_reparateur_pro",
        "ds_v": "db_mon_reparateur_pro",
    },
    {
        "source": "clinique_phone",
        "name": "Clinique Phone Rosny-sous-Bois",
        "domain": "Réparations Multi-Marques",
        "icon": "🏥",
        "prefix": "clinique_phone_repair_prices",
        "ds_name": "db_clinique_phone",
        "ds_v": "db_clinique_phone",
    },
    {
        "source": "go_smart",
        "name": "GO SMART Boulogne",
        "domain": "Réparations Multi-Marques",
        "icon": "⚡",
        "prefix": "go_smart_repair_prices",
        "ds_name": "db_go_smart",
        "ds_v": "db_go_smart",
    },
]


# Sources auditées et définitivement écartées (non scrapables ou sans grille tarifaire)
EXCLUDED_SOURCES = {
    "mistral_phone",
    "stuff_telecom",
    "smartphonepro",
    "i_rep",
    "mister_minit",
}


def get_dynamic_sources_config() -> list[dict[str, Any]]:
    """
    Retourne la liste des configurations de sources en combinant les sources déclarées
    et les sources découvertes dynamiquement dans dataset/, scraper/outputs/ et liste_des_web/outputs/.
    Garantit que chaque nouveau scraping crée sa propre source isolée et n'écrase jamais une ancienne source.
    """
    import re
    known = list(SOURCES_CONFIG)
    known_sources = {c["source"] for c in known}
    known_prefixes = {c["prefix"] for c in known}

    search_dirs = [INPUT_DATASET_DIR, INPUT_OUTPUTS_DIR, LISTE_DES_WEB_OUTPUTS_DIR]
    for d in search_dirs:
        if not d.exists():
            continue
        for p in sorted(d.glob("*.csv")):
            if p.is_symlink() or p.name.startswith(".") or "_ai_refined" in p.name:
                continue

            # Ne jamais réintégrer les sources auditées et écartées
            if any(exc in p.name for exc in EXCLUDED_SOURCES):
                continue

            # Cas 1: Déjà couvert par un préfixe existant
            if any(p.name.startswith(pfx) for pfx in known_prefixes):
                continue

            # Cas 2: Fichier de production db_*.csv non déclaré (sans timestamp)
            if p.name.startswith("db_"):
                if re.search(r"\d{8}", p.name):
                    continue
                src_key = p.stem.replace("db_", "")
                base_key = (
                    src_key
                    .replace("_repair_prices", "")
                    .replace("_official_prices", "")
                    .replace("_market_prices", "")
                    .replace("_part_costs", "")
                    .replace("_smartphones", "")
                    .replace("_prices", "")
                )
                if src_key in known_sources or base_key in known_sources or src_key.startswith("scraperV") or src_key in EXCLUDED_SOURCES:
                    continue
                nrows, ncols, _, _ = get_csv_stats(p)
                dom = "Vente / Marché" if ncols == 23 else "Réparation / Pièces"
                entry = {
                    "source": src_key,
                    "name": src_key.replace("_", " ").title(),
                    "domain": dom,
                    "icon": "📱" if ncols == 23 else "🔧",
                    "prefix": f"db_{src_key}",
                    "ds_name": f"db_{src_key}",
                    "ds_v": f"db_{src_key}",
                }
                known.append(entry)
                known_sources.add(src_key)
                known_prefixes.add(entry["prefix"])
                continue

            # Cas 3: Fichier outputs [nom]_[timestamp].csv dynamique pour futurs scrapers
            m = re.match(r"^(.+?)_\d{8,14}(?:[_\+]\d+)?\.csv$", p.name)
            extracted_prefix = m.group(1) if m else p.stem

            if extracted_prefix in known_prefixes or any(exc in extracted_prefix for exc in EXCLUDED_SOURCES):
                continue

            # Clé de source normalisée
            src_key = (
                extracted_prefix
                .replace("_repair_prices", "")
                .replace("_official_prices", "")
                .replace("_market_prices", "")
                .replace("_part_costs", "")
                .replace("_smartphones", "")
                .replace("_prices", "")
            )
            if src_key in known_sources or extracted_prefix in known_sources:
                continue

            if src_key not in known_sources:
                nrows, ncols, _, _ = get_csv_stats(p)
                dom = "Vente / Marché" if ncols == 23 else "Réparation / Pièces"
                entry = {
                    "source": src_key,
                    "name": src_key.replace("_", " ").title(),
                    "domain": dom,
                    "icon": "📱" if ncols == 23 else "🔧",
                    "prefix": extracted_prefix,
                    "ds_name": f"db_{src_key}",
                    "ds_v": f"db_{src_key}",
                }
                known.append(entry)
                known_sources.add(src_key)
                known_prefixes.add(extracted_prefix)

    return known


# État global du serveur
current_normalizer: AsyncAINormalizer | None = None
current_task: asyncio.Task | None = None
active_job_info: dict[str, Any] = {
    "status": "idle",
    "filename": "",
    "model": DEFAULT_MODEL,
    "progress_percent": 0.0,
    "processed_rows": 0,
    "cached_rows": 0,
    "delta_rows": 0,
    "total_rows": 0,
    "modified_rows": 0,
    "filtered_accessories": 0,
    "speed_rows_per_sec": 0.0,
    "incremental": True,
    "status_message": "En attente",
    "recent_corrections": [],
    "result": None,
}


def get_csv_stats(path: Path | None) -> tuple[int, int, float, str]:
    """Retourne (rows_count, columns_count, size_kb, mtime_formatted)."""
    if not path or not path.exists():
        return 0, 0, 0.0, "-"
    sz = path.stat().st_size
    mtime_str = datetime.datetime.fromtimestamp(path.stat().st_mtime).strftime("%d/%m/%Y %H:%M")
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            reader = csv.reader(f)
            header = next(reader, [])
            ncols = len(header)
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            nrows = max(0, sum(1 for _ in f) - 1)
    except Exception:
        ncols = 0
        nrows = 0
    return nrows, ncols, round(sz / 1024, 1), mtime_str


def scan_source_files(scope: str = "dataset") -> list[dict[str, Any]]:
    """
    Scanne les fichiers de données selon le périmètre désiré :
    - 'dataset' (par défaut) : Affiche strictement la base de données de production officielle
    - 'outputs' : Affiche les fichiers bruts issus des scrapers dans scraper/outputs
    - 'all' : Affiche la totalité
    """
    files_info = []

    # 1. Base de données de Production (./dataset/) — Source officielle certifiée
    if scope in ("dataset", "all") and INPUT_DATASET_DIR.exists():
        for csv_path in sorted(INPUT_DATASET_DIR.glob("db_*.csv")):
            if csv_path.is_symlink():
                continue
            nrows, ncols, sz_kb, mtime_str = get_csv_stats(csv_path)
            domain = "Vente / Marché" if ncols == 23 else "Réparation / Pièces"
            source = csv_path.stem.replace("db_", "")
            if source in EXCLUDED_SOURCES or source.lower().startswith("scraperv"):
                continue

            refined_pattern = f"{source}_ai_refined_*.csv"
            has_refined = len(list(OUTPUT_AI_DIR.glob(refined_pattern))) > 0

            # Trouver label convivial
            dynamic_sources = get_dynamic_sources_config()
            cfg = next((c for c in dynamic_sources if c["source"] == source), None)
            display_name = cfg["name"] if cfg else source.replace("_", " ").title()
            icon = cfg["icon"] if cfg else ("📱" if ncols == 23 else "📁")

            files_info.append({
                "source": source,
                "display_name": display_name,
                "icon": icon,
                "filename": csv_path.name,
                "full_path": str(csv_path),
                "location": "dataset",
                "is_production": True,
                "domain": domain,
                "columns_count": ncols,
                "rows_count": nrows,
                "size_kb": sz_kb,
                "mtime": mtime_str,
                "is_refined": has_refined,
            })

    # 2. Zone de Staging / Recette (scraper/outputs/)
    if scope in ("outputs", "all") and INPUT_OUTPUTS_DIR.exists():
        for csv_path in sorted(INPUT_OUTPUTS_DIR.glob("*.csv")):
            if csv_path.name.startswith("."):
                continue
            nrows, ncols, sz_kb, mtime_str = get_csv_stats(csv_path)
            domain = "Vente / Marché" if ncols == 23 else "Réparation / Pièces"
            dynamic_sources = get_dynamic_sources_config()
            cfg = None
            for c in dynamic_sources:
                if csv_path.stem.startswith(c["prefix"]) or (c["prefix"].startswith("db_") and csv_path.stem.startswith(c["source"])):
                    cfg = c
                    break

            if cfg:
                source = cfg["source"]
                display_name = cfg["name"]
                icon = cfg["icon"]
                domain = cfg["domain"]
            else:
                source = csv_path.stem.split("_")[0]
                if csv_path.stem.startswith("phone_cbd"):
                    source = "phone_cbd"
                display_name = source.replace("_", " ").title()
                icon = "📄"

            refined_pattern = f"{source}_ai_refined_*.csv"
            has_refined = len(list(OUTPUT_AI_DIR.glob(refined_pattern))) > 0

            files_info.append({
                "source": source,
                "display_name": display_name,
                "icon": icon,
                "filename": csv_path.name,
                "full_path": str(csv_path),
                "location": "scraper/outputs",
                "is_production": False,
                "domain": domain,
                "columns_count": ncols,
                "rows_count": nrows,
                "size_kb": sz_kb,
                "mtime": mtime_str,
                "is_refined": has_refined,
            })

    return files_info


# ==============================================================================
# ENDPOINTS REST API
# ==============================================================================

async def handle_api_status(request: web.Request) -> web.Response:
    """Retourne l'état du système et de la machine."""
    normalizer = AsyncAINormalizer()
    ollama_ok, ollama_msg = await normalizer.check_ollama_health()

    return web.json_response({
        "machine": "Mac mini M4 (16 Go RAM)",
        "engine": "Ollama (Metal GPU)",
        "ollama_active": ollama_ok,
        "ollama_message": ollama_msg,
        "default_model": DEFAULT_MODEL,
        "available_models": AVAILABLE_MODELS,
        "job_status": active_job_info["status"],
    })


async def handle_api_files(request: web.Request) -> web.Response:
    """Liste les fichiers sources disponibles. Par défaut : scope='dataset' (production uniquement)."""
    scope = request.query.get("scope", "dataset")
    files = scan_source_files(scope=scope)
    return web.json_response({"files": files, "count": len(files), "scope": scope})


async def handle_api_staging_compare(request: web.Request) -> web.Response:
    """Compare l'état des runs dans scraper/outputs/ et liste_des_web/outputs/ avec la production dans ./dataset/."""
    comparisons = []
    dynamic_sources = get_dynamic_sources_config()
    stats_summary = {
        "total_sources": len(dynamic_sources),
        "up_to_date": 0,
        "enriched": 0,
        "recent": 0,
        "partial": 0,
    }

    for cfg in dynamic_sources:
        # Trouver le run le plus récent dans outputs (scraper/outputs et liste_des_web/outputs)
        prefix = cfg["prefix"]
        candidates = []
        for out_dir in [INPUT_OUTPUTS_DIR, LISTE_DES_WEB_OUTPUTS_DIR]:
            if out_dir.exists():
                candidates.extend(out_dir.glob(f"{prefix}_*.csv"))
                candidates.extend(out_dir.glob(f"{prefix}.csv"))
                if prefix.startswith("db_"):
                    candidates.extend(out_dir.glob(f"db_{cfg['source']}.csv"))
        candidates = sorted(list({p.resolve(): p for p in candidates}.values()), key=lambda p: p.stat().st_mtime)
        latest_out = candidates[-1] if candidates else None
        out_rows, out_cols, out_sz, out_date = get_csv_stats(latest_out)

        # Production actuelle dans dataset
        ds_name = cfg["ds_name"]
        ds_csv = INPUT_DATASET_DIR / f"{ds_name}.csv"
        ds_rows, ds_cols, ds_sz, ds_date = get_csv_stats(ds_csv)

        diff_rows = out_rows - ds_rows

        # Déterminer statut
        if out_rows == 0 and ds_rows == 0:
            status = "MANQUANT"
            label = "Aucun fichier disponible"
            badge = "gray"
            can_promote = False
            needs_force = False
        elif out_rows == 0:
            status = "PAS_DE_STAGING"
            label = "Aucun run en staging"
            badge = "gray"
            can_promote = False
            needs_force = False
        elif ds_rows == 0:
            status = "NOUVELLE_SOURCE"
            label = "Nouvelle source (Prod vide)"
            badge = "cyan"
            can_promote = True
            needs_force = False
            stats_summary["enriched"] += 1
        elif diff_rows == 0:
            status = "A_JOUR"
            label = "Production à jour (100% conforme)"
            badge = "emerald"
            can_promote = True
            needs_force = False
            stats_summary["up_to_date"] += 1
        elif diff_rows > 0:
            status = "ENRICHI"
            label = f"Nouveau run enrichi (+{diff_rows} offres)"
            badge = "cyan"
            can_promote = True
            needs_force = False
            stats_summary["enriched"] += 1
        elif out_rows >= (ds_rows * 0.5):
            status = "RECENT"
            label = f"Nouveau run récent ({diff_rows} offres)"
            badge = "amber"
            can_promote = True
            needs_force = False
            stats_summary["recent"] += 1
        else:
            status = "TEST_PARTIEL"
            label = f"⚠️ Test partiel ({out_rows} vs {ds_rows} lignes)"
            badge = "rose"
            can_promote = True
            needs_force = True
            stats_summary["partial"] += 1

        comparisons.append({
            "source_key": cfg["source"],
            "name": cfg["name"],
            "domain": cfg["domain"],
            "icon": cfg["icon"],
            "prefix": cfg["prefix"],
            "ds_name": cfg["ds_name"],
            "ds_v": cfg["ds_v"],
            "staging": {
                "filename": latest_out.name if latest_out else None,
                "full_path": str(latest_out) if latest_out else None,
                "rows": out_rows,
                "cols": out_cols,
                "size_kb": out_sz,
                "date": out_date,
            },
            "production": {
                "filename": ds_csv.name if ds_csv.exists() else None,
                "full_path": str(ds_csv) if ds_csv.exists() else None,
                "rows": ds_rows,
                "cols": ds_cols,
                "size_kb": ds_sz,
                "date": ds_date,
            },
            "diff_rows": diff_rows,
            "status": status,
            "status_label": label,
            "status_badge": badge,
            "can_promote": can_promote,
            "needs_force": needs_force,
        })

    return web.json_response({
        "summary": stats_summary,
        "sources": comparisons,
    })


async def handle_api_staging_promote(request: web.Request) -> web.Response:
    """Promut un run de scraper/outputs/ ou liste_des_web/outputs/ vers ./dataset/ avec mise à jour des symlinks."""
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Payload JSON invalide."}, status=400)

    source_key = data.get("source")
    force = bool(data.get("force", False))

    dynamic_sources = get_dynamic_sources_config()
    cfg = next((c for c in dynamic_sources if c["source"] == source_key), None)
    if not cfg:
        return web.json_response({"error": f"Source inconnue : '{source_key}'"}, status=404)

    # Récupérer le dernier run
    prefix = cfg["prefix"]
    candidates = []
    for out_dir in [INPUT_OUTPUTS_DIR, LISTE_DES_WEB_OUTPUTS_DIR]:
        if out_dir.exists():
            candidates.extend(out_dir.glob(f"{prefix}_*.csv"))
            candidates.extend(out_dir.glob(f"{prefix}.csv"))
            if prefix.startswith("db_"):
                candidates.extend(out_dir.glob(f"db_{cfg['source']}.csv"))
    candidates = sorted(list({p.resolve(): p for p in candidates}.values()), key=lambda p: p.stat().st_mtime)
    if not candidates:
        return web.json_response({"error": f"Aucun fichier de run trouvé pour '{cfg['name']}' dans outputs."}, status=404)

    latest_csv = candidates[-1]
    stem = latest_csv.stem
    source_dir = latest_csv.parent

    out_rows, _, out_sz, out_date = get_csv_stats(latest_csv)
    ds_csv = INPUT_DATASET_DIR / f"{cfg['ds_name']}.csv"
    ds_rows, _, _, ds_date = get_csv_stats(ds_csv)

    # Sécurité anti-écrasement
    if ds_rows > 0 and out_rows < (ds_rows * 0.5) and not force:
        return web.json_response({
            "error": (
                f"SÉCURITÉ ANTI-ÉCRASEMENT : Le fichier de staging ne contient que {out_rows} lignes "
                f"contre {ds_rows} lignes actuellement en production ({out_rows - ds_rows} lignes). "
                f"Il semble s'agir d'un run de test partiel. Cochez l'option 'Forcer la promotion' "
                f"si vous souhaitez tout de même remplacer la base officielle."
            ),
            "needs_force": True,
            "staging_rows": out_rows,
            "dataset_rows": ds_rows,
        }, status=400)

    # Copie des 5 fichiers
    file_types = [
        (f"{stem}.csv", f"{cfg['ds_name']}.csv", f"{cfg['ds_v']}.csv"),
        (f"{stem}.json", f"{cfg['ds_name']}.json", f"{cfg['ds_v']}.json"),
        (f"{stem}.jsonl", f"{cfg['ds_name']}.jsonl", f"{cfg['ds_v']}.jsonl"),
        (f"{stem}_run.json", f"{cfg['ds_name']}_run.json", f"{cfg['ds_v']}_run.json"),
        (f"{stem}_issues.jsonl", f"{cfg['ds_name']}_issues.jsonl", f"{cfg['ds_v']}_issues.jsonl"),
    ]

    copied_files = []
    for src_name, dst_name, symlink_name in file_types:
        src_path = source_dir / src_name
        dst_path = INPUT_DATASET_DIR / dst_name
        if src_path.exists():
            shutil.copy2(src_path, dst_path)
            copied_files.append(dst_name)

        # Mettre à jour le lien symbolique relatif si différent du fichier principal
        if symlink_name != dst_name:
            symlink_path = INPUT_DATASET_DIR / symlink_name
            if symlink_path.is_symlink() or symlink_path.exists():
                symlink_path.unlink()
            os.symlink(dst_name, symlink_path)


    return web.json_response({
        "success": True,
        "message": f"Source '{cfg['name']}' promue avec succès vers la base de production ({out_rows} lignes).",
        "source": cfg["name"],
        "rows": out_rows,
        "copied_files": copied_files,
    })


async def handle_api_progress(request: web.Request) -> web.Response:
    """Retourne les métriques de progression en direct."""
    return web.json_response(active_job_info)


async def handle_api_outputs(request: web.Request) -> web.Response:
    """Liste les fichiers générés dans scraper/outputs_ai."""
    refined_files = []
    if OUTPUT_AI_DIR.exists():
        for p in sorted(OUTPUT_AI_DIR.glob("*"), reverse=True):
            if p.is_file() and not p.name.startswith("."):
                refined_files.append({
                    "name": p.name,
                    "extension": p.suffix,
                    "size_kb": round(p.stat().st_size / 1024, 1),
                    "modified_at": p.stat().st_mtime,
                })
    return web.json_response({"outputs": refined_files, "count": len(refined_files)})


async def handle_api_start(request: web.Request) -> web.Response:
    """Déclenche la normalisation asynchrone sans bloquer."""
    global current_normalizer, current_task, active_job_info

    if active_job_info["status"] == "running":
        return web.json_response({"error": "Un traitement est déjà en cours."}, status=400)

    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Payload JSON invalide."}, status=400)

    file_path = data.get("file_path")
    model_name = data.get("model", DEFAULT_MODEL)
    batch_size = int(data.get("batch_size", DEFAULT_BATCH_SIZE))
    concurrency = int(data.get("concurrency", DEFAULT_CONCURRENCY))
    incremental = bool(data.get("incremental", True))

    if not file_path or not Path(file_path).exists():
        return web.json_response({"error": "Chemin de fichier introuvable."}, status=404)

    target_path = Path(file_path)

    # Réinitialisation de l'état
    active_job_info["status"] = "running"
    active_job_info["filename"] = target_path.name
    active_job_info["model"] = model_name
    active_job_info["incremental"] = incremental
    active_job_info["progress_percent"] = 0.0
    active_job_info["processed_rows"] = 0
    active_job_info["cached_rows"] = 0
    active_job_info["delta_rows"] = 0
    active_job_info["total_rows"] = 0
    active_job_info["modified_rows"] = 0
    active_job_info["filtered_accessories"] = 0
    active_job_info["speed_rows_per_sec"] = 0.0
    active_job_info["status_message"] = "Démarrage de l'agent IA..."
    active_job_info["recent_corrections"] = []
    active_job_info["result"] = None

    current_normalizer = AsyncAINormalizer(
        model=model_name,
        concurrency=concurrency,
        batch_size=batch_size,
    )

    def on_progress(p_data: dict[str, Any]):
        active_job_info.update(p_data)

    async def run_in_background():
        global active_job_info
        try:
            res = await current_normalizer.normalize_file(
                target_path,
                progress_callback=on_progress,
                incremental=incremental,
            )
            active_job_info["status"] = "completed"
            active_job_info["status_message"] = "Normalisation terminée avec succès !"
            active_job_info["result"] = res
        except Exception as exc:
            active_job_info["status"] = "error"
            active_job_info["status_message"] = f"Erreur : {exc}"

    current_task = asyncio.create_task(run_in_background())

    return web.json_response({
        "message": "Normalisation lancée avec succès en arrière-plan.",
        "file": target_path.name,
        "model": model_name,
        "incremental": incremental,
    })


async def handle_api_stop(request: web.Request) -> web.Response:
    """Arrête le traitement en cours."""
    global current_normalizer, active_job_info
    if current_normalizer and current_normalizer.is_running:
        current_normalizer.stop()
        active_job_info["status"] = "stopped"
        active_job_info["status_message"] = "Arrêté par l'utilisateur."
        return web.json_response({"message": "Arrêt demandé."})
    return web.json_response({"message": "Aucun traitement actif."})


# ==============================================================================
# GESTION ET API DU CATALOGUE UNIFIÉ 360° (UNIFIED CATALOG)
# ==============================================================================

_cached_unified_db: dict[str, Any] | None = None
_cached_unified_stats: dict[str, Any] | None = None
_cached_device_list: list[dict[str, Any]] | None = None
_cached_unified_mtime: float = 0.0


def get_unified_data() -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    """Charge en mémoire cache la base unifiée et calcule les statistiques 360°."""
    global _cached_unified_db, _cached_unified_stats, _cached_device_list, _cached_unified_mtime

    if not UNIFIED_DB_PATH.exists():
        return {}, {"error": "Base unified_catalog_database.json non trouvée"}, []

    current_mtime = UNIFIED_DB_PATH.stat().st_mtime
    if _cached_unified_db is not None and current_mtime == _cached_unified_mtime:
        return _cached_unified_db, _cached_unified_stats, _cached_device_list

    try:
        with open(UNIFIED_DB_PATH, "r", encoding="utf-8") as f:
            db = json.load(f)
    except Exception as exc:
        return {}, {"error": f"Erreur de lecture JSON : {exc}"}, []

    total_skus = len(db)
    brands_counter = Counter()
    brand_models = defaultdict(set)
    brand_repair_counts = Counter()
    brand_sales_counts = Counter()

    repair_cat_counts = Counter()
    repair_cat_prices = defaultdict(list)
    repair_cat_models = defaultdict(set)

    model_repairs_map = Counter()
    model_sales_map = Counter()

    providers_counts = Counter()
    b2b_parts_total = 0
    sales_conditions = Counter()

    light_items = []

    CATEGORIES_META = {
        "ecran": {"label": "Remplacement Écran / Vitre", "icon": "📱"},
        "vitre_arriere": {"label": "Remplacement Vitre Arrière", "icon": "🔙"},
        "batterie": {"label": "Changement Batterie", "icon": "🔋"},
        "connecteur_charge": {"label": "Connecteur de Charge", "icon": "⚡"},
        "camera_arriere": {"label": "Caméra Arrière", "icon": "📷"},
        "camera_avant": {"label": "Caméra Avant / Selfie", "icon": "🤳"},
        "haut_parleur": {"label": "Haut-parleur & Écouteur", "icon": "🔊"},
        "vibreur": {"label": "Remplacement Vibreur", "icon": "📳"},
        "desoxydation": {"label": "Désoxydation Liquide", "icon": "💧"},
        "diagnostic": {"label": "Diagnostic Atelier & Devis", "icon": "🩺"},
        "autre": {"label": "Autres Prestations", "icon": "🛠️"},
    }

    for dev_id, dev in db.items():
        brand = dev.get("brand", "Autre")
        model = dev.get("model", "Inconnu")
        storage = dev.get("storage")
        colors = dev.get("available_colors", [])

        brands_counter[brand] += 1
        brand_models[brand].add(model)

        # Sales
        ms = dev.get("market_sales", {})
        total_sales_dev = 0
        min_prices = {}
        for cond in ["new", "refurbished", "used"]:
            cond_data = ms.get(cond, {})
            offers = cond_data.get("all_offers", [])
            cnt = len(offers)
            total_sales_dev += cnt
            sales_conditions[cond] += cnt
            brand_sales_counts[brand] += cnt
            if cnt > 0:
                model_sales_map[f"{brand} {model}"] += cnt

            p_min = cond_data.get("summary", {}).get("min_price")
            if p_min is not None:
                min_prices[cond] = p_min

            for off in offers:
                src = off.get("source_site", "marché")
                providers_counts[src] += 1

        # Repairs
        rep = dev.get("repairs", {})
        total_repairs_dev = 0
        total_parts_dev = 0
        min_rep_prices = {}
        min_b2b_part_price = None

        for cat_id, cat_obj in rep.items():
            provs = cat_obj.get("all_providers", [])
            parts = cat_obj.get("b2b_parts", [])
            b2b_parts_total += len(parts)
            total_parts_dev += len(parts)

            for pt in parts:
                p_ht = pt.get("price_ht")
                if p_ht is not None:
                    try:
                        p_ht_val = float(p_ht)
                        if p_ht_val > 0:
                            if min_b2b_part_price is None or p_ht_val < min_b2b_part_price:
                                min_b2b_part_price = p_ht_val
                    except Exception:
                        pass

            p_cnt = len(provs)
            total_repairs_dev += p_cnt
            repair_cat_counts[cat_id] += p_cnt
            brand_repair_counts[brand] += p_cnt
            if p_cnt > 0:
                model_repairs_map[f"{brand} {model}"] += p_cnt
                repair_cat_models[cat_id].add(f"{brand} {model}")

            p_min = cat_obj.get("summary", {}).get("min_price")
            if p_min is not None:
                min_rep_prices[cat_id] = p_min

            for p in provs:
                shop = p.get("shop_name", "Atelier Réparation")
                providers_counts[shop] += 1
                if p.get("price") is not None:
                    try:
                        p_val = float(p["price"])
                        if p_val > 0:
                            repair_cat_prices[cat_id].append(p_val)
                    except Exception:
                        pass

        # Calcul du meilleur tarif atelier disponible sur n'importe quelle catégorie
        best_rep_price = None
        best_rep_label = None
        best_rep_icon = None
        for cat_id in ["ecran", "batterie", "vitre_arriere", "connecteur_charge", "camera_arriere", "camera_avant", "haut_parleur", "vibreur", "desoxydation", "diagnostic", "autre"]:
            if cat_id in min_rep_prices:
                best_rep_price = min_rep_prices[cat_id]
                best_rep_label = CATEGORIES_META.get(cat_id, {}).get("label", cat_id)
                best_rep_icon = CATEGORIES_META.get(cat_id, {}).get("icon", "🛠️")
                break

        light_items.append({
            "device_id": dev_id,
            "brand": brand,
            "model": model,
            "storage": storage,
            "colors_count": len(colors),
            "colors_preview": colors[:4],
            "total_sales": total_sales_dev,
            "total_repairs": total_repairs_dev,
            "total_b2b_parts": total_parts_dev,
            "min_new_price": min_prices.get("new"),
            "min_refurb_price": min_prices.get("refurbished"),
            "min_used_price": min_prices.get("used"),
            "min_screen_price": min_rep_prices.get("ecran"),
            "min_battery_price": min_rep_prices.get("batterie"),
            "min_back_price": min_rep_prices.get("vitre_arriere"),
            "best_repair_price": best_rep_price,
            "best_repair_icon": best_rep_icon,
            "best_repair_label": best_rep_label,
            "min_b2b_part_price": round(min_b2b_part_price, 2) if min_b2b_part_price is not None else None,
        })

    # Synthèse des 11 catégories de réparation
    repair_categories_summary = []
    for cat_id, meta in CATEGORIES_META.items():
        prices = repair_cat_prices[cat_id]
        repair_categories_summary.append({
            "id": cat_id,
            "label": meta["label"],
            "icon": meta["icon"],
            "total_offers": repair_cat_counts[cat_id],
            "models_covered": len(repair_cat_models[cat_id]),
            "min_price": round(min(prices), 2) if prices else None,
            "avg_price": round(statistics.mean(prices), 2) if prices else None,
            "max_price": round(max(prices), 2) if prices else None,
        })

    # Synthèse des marques
    brands_summary = []
    for brand, skus_cnt in brands_counter.most_common():
        brands_summary.append({
            "brand": brand,
            "skus_count": skus_cnt,
            "models_count": len(brand_models[brand]),
            "repairs_count": brand_repair_counts[brand],
            "sales_count": brand_sales_counts[brand],
        })

    # Top modèles
    top_repaired_models = [{"model": m, "count": c} for m, c in model_repairs_map.most_common(12)]
    top_sold_models = [{"model": m, "count": c} for m, c in model_sales_map.most_common(12)]

    stats = {
        "total_skus": total_skus,
        "total_models": sum(len(m_set) for m_set in brand_models.values()),
        "total_brands": len(brands_counter),
        "total_sales_offers": sum(sales_conditions.values()),
        "sales_by_condition": dict(sales_conditions),
        "total_repairs": sum(repair_cat_counts.values()),
        "total_b2b_parts": b2b_parts_total,
        "providers_counts": dict(providers_counts.most_common(15)),
        "repair_categories": repair_categories_summary,
        "brands_summary": brands_summary,
        "top_repaired_models": top_repaired_models,
        "top_sold_models": top_sold_models,
        "db_file_size_mb": round(UNIFIED_DB_PATH.stat().st_size / (1024 * 1024), 2),
        "last_modified": datetime.datetime.fromtimestamp(current_mtime, tz=datetime.timezone.utc).isoformat(),
    }

    _cached_unified_db = db
    _cached_unified_stats = stats
    _cached_device_list = light_items
    _cached_unified_mtime = current_mtime

    return _cached_unified_db, _cached_unified_stats, _cached_device_list


async def handle_api_unified_stats(request: web.Request) -> web.Response:
    """Retourne les métriques statistiques globales du catalogue unifié."""
    _, stats, _ = get_unified_data()
    return web.json_response(stats)


async def handle_api_unified_search(request: web.Request) -> web.Response:
    """Recherche et filtre paginés dans le catalogue unifié."""
    _, _, items = get_unified_data()

    query = request.query.get("q", "").strip().lower()
    brand_filter = request.query.get("brand", "").strip().lower()
    storage_filter = request.query.get("storage", "").strip()
    has_sales = request.query.get("has_sales", "").strip().lower() == "true"
    has_repairs = request.query.get("has_repairs", "").strip().lower() == "true"

    try:
        page = max(1, int(request.query.get("page", 1)))
        limit = min(100, max(5, int(request.query.get("limit", 25))))
    except ValueError:
        page = 1
        limit = 25

    filtered = items

    if brand_filter and brand_filter != "all":
        filtered = [it for it in filtered if it["brand"].lower() == brand_filter]

    if storage_filter and storage_filter != "all":
        try:
            s_val = int(storage_filter)
            filtered = [it for it in filtered if it["storage"] == s_val]
        except ValueError:
            pass

    if has_sales:
        filtered = [it for it in filtered if it["total_sales"] > 0]

    if has_repairs:
        filtered = [it for it in filtered if it["total_repairs"] > 0]

    if query:
        terms = query.split()
        def match_item(it: dict[str, Any]) -> bool:
            target_str = f"{it['brand']} {it['model']} {it.get('storage') or ''} {it['device_id']}".lower()
            return all(t in target_str for t in terms)
        filtered = [it for it in filtered if match_item(it)]

    total_results = len(filtered)
    start_idx = (page - 1) * limit
    end_idx = start_idx + limit
    page_items = filtered[start_idx:end_idx]

    return web.json_response({
        "total": total_results,
        "page": page,
        "limit": limit,
        "total_pages": max(1, (total_results + limit - 1) // limit),
        "items": page_items,
    })


async def handle_api_unified_device(request: web.Request) -> web.Response:
    """Retourne le document JSON canonique 360° complet d'un SKU."""
    device_id = request.query.get("device_id", "").strip()
    if not device_id:
        return web.json_response({"error": "Paramètre device_id manquant"}, status=400)

    db, _, _ = get_unified_data()
    device_data = db.get(device_id)

    if not device_data:
        doc_file = UNITED_DATASET_DIR / "devices" / f"{device_id}.json"
        if doc_file.exists():
            try:
                with open(doc_file, "r", encoding="utf-8") as f:
                    device_data = json.load(f)
            except Exception:
                pass

    if not device_data:
        return web.json_response({"error": f"Appareil '{device_id}' non trouvé dans le catalogue"}, status=404)

    return web.json_response(device_data)


async def handle_api_unified_rebuild(request: web.Request) -> web.Response:
    """Relance le script de fusion unifié pour recalculer la base avec les dernières données."""
    global _cached_unified_db, _cached_unified_stats, _cached_device_list
    if not MERGE_SCRIPT_PATH.exists():
        return web.json_response({"error": "Script de fusion introuvable"}, status=500)

    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            str(MERGE_SCRIPT_PATH),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            return web.json_response({
                "error": f"Échec du merge unifié (code {proc.returncode})",
                "details": stderr.decode("utf-8", errors="replace"),
            }, status=500)

        # Invalider le cache pour forcer le recalcul
        _cached_unified_db = None
        _cached_unified_stats = None
        _cached_device_list = None
        _, stats, _ = get_unified_data()

        return web.json_response({
            "message": "Catalogue unifié reconstruit avec succès !",
            "stats": stats,
            "output": stdout.decode("utf-8", errors="replace"),
        })
    except Exception as exc:
        return web.json_response({"error": f"Erreur lors de la reconstruction : {exc}"}, status=500)


# ==============================================================================
# ROUTE PRINCIPALE DE L'INTERFACE WEB
# ==============================================================================

async def handle_index(request: web.Request) -> web.Response:
    """Sert l'interface HTML principale du Dashboard."""
    html_path = Path(__file__).parent / "static" / "index.html"
    if html_path.exists():
        with open(html_path, "r", encoding="utf-8") as f:
            return web.Response(text=f.read(), content_type="text/html")
    return web.Response(text="Dashboard en cours de construction...", content_type="text/plain")


def create_app() -> web.Application:
    """Crée et configure l'application aiohttp."""
    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_get("/api/status", handle_api_status)
    app.router.add_get("/api/files", handle_api_files)
    app.router.add_get("/api/staging/compare", handle_api_staging_compare)
    app.router.add_post("/api/staging/promote", handle_api_staging_promote)
    app.router.add_get("/api/progress", handle_api_progress)
    app.router.add_get("/api/outputs", handle_api_outputs)
    app.router.add_post("/api/start", handle_api_start)
    app.router.add_post("/api/stop", handle_api_stop)
    # Routes Catalogue Unifié 360°
    app.router.add_get("/api/unified/stats", handle_api_unified_stats)
    app.router.add_get("/api/unified/search", handle_api_unified_search)
    app.router.add_get("/api/unified/device", handle_api_unified_device)
    app.router.add_post("/api/unified/rebuild", handle_api_unified_rebuild)
    return app


def run_dashboard(port: int = DASHBOARD_PORT, host: str = DASHBOARD_HOST) -> None:
    """Lance le serveur de Dashboard Web."""
    app = create_app()
    print(f"\n{'='*70}")
    print(f"🚀 DASHBOARD IA E-COMMERCE AI V2 DÉMARRÉ SUR : http://{host}:{port}")
    print(f"{'='*70}\n")
    web.run_app(app, host=host, port=port)


if __name__ == "__main__":
    run_dashboard()
