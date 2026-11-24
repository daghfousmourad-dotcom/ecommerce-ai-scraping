#!/usr/bin/env python3
"""
run_normalizer.py — Déclencheur CLI du Pipeline de Normalisation IA E-commerce AI V2.
Usage :
    python3 run_normalizer.py --source apple
    python3 run_normalizer.py --all
    python3 run_normalizer.py --list
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
OUTPUTS_DIR = PROJECT_ROOT / "02_scrapers" / "outputs"
AI_OUTPUTS_DIR = CURRENT_DIR / "outputs"

# Ajouter le dossier engine au sys.path
sys.path.insert(0, str(CURRENT_DIR / "engine"))
sys.path.insert(0, str(PROJECT_ROOT / "02_scrapers"))

try:
    from engine import AsyncAINormalizer
except ImportError as e:
    print(f"⚠️ Erreur d'import AsyncAINormalizer : {e}")
    AsyncAINormalizer = None


def find_source_files() -> dict[str, Path]:
    """Trouve les fichiers CSV bruts disponibles dans 02_scrapers/outputs/."""
    sources = {}
    if not OUTPUTS_DIR.exists():
        return sources

    for f in sorted(OUTPUTS_DIR.glob("db_*.csv")):
        source_name = f.stem.replace("db_", "")
        sources[source_name] = f
    return sources


async def normalize_source(source_name: str, csv_path: Path, model: str = "qwen2.5:3b") -> bool:
    print(f"\n🧠 Normalisation IA pour : {source_name.upper()}")
    print(f"📄 Fichier source : {csv_path}")

    if AsyncAINormalizer is None:
        print("❌ Moteur IA non initialisé.")
        return False

    normalizer = AsyncAINormalizer(model=model, output_dir=AI_OUTPUTS_DIR)
    try:
        res = await normalizer.normalize_dataset(
            input_csv_path=csv_path,
            source_site=source_name,
        )
        print(f"✅ Succès : {res.get('output_rows', 0)} lignes traitées.")
        return True
    except Exception as e:
        print(f"❌ Erreur lors de la normalisation : {e}")
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Pipeline de Normalisation IA E-commerce AI V2")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--source", type=str, help="Nom de la source à normaliser (ex: apple, save...)")
    group.add_argument("--all", action="store_true", help="Normaliser toutes les sources disponibles")
    group.add_argument("--list", action="store_true", help="Lister les fichiers disponibles pour normalisation")

    parser.add_argument("--model", type=str, default="qwen2.5:3b", help="Modèle Ollama (défaut: qwen2.5:3b)")
    args = parser.parse_args()

    available = find_source_files()

    if args.list:
        print("\n📋 FICHIERS DE SCRAPING PRÊTS POUR NORMALISATION :")
        print("-" * 60)
        for name, path in available.items():
            size_kb = path.stat().st_size / 1024
            print(f"  • {name:<22} -> {path.name} ({size_kb:.1f} Ko)")
        print(f"\nTotal : {len(available)} fichiers trouvés dans {OUTPUTS_DIR.relative_to(PROJECT_ROOT)}.")
        return

    if args.source:
        source_key = args.source.lower().strip()
        if source_key not in available:
            print(f"❌ Fichier pour la source '{args.source}' introuvable dans {OUTPUTS_DIR}")
            sys.exit(1)
        ok = asyncio.run(normalize_source(source_key, available[source_key], model=args.model))
        sys.exit(0 if ok else 1)

    if args.all:
        print(f"\n🚀 Lancement de la normalisation IA pour {len(available)} sources...")
        for name, path in available.items():
            asyncio.run(normalize_source(name, path, model=args.model))


if __name__ == "__main__":
    main()
