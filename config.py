"""
config.py — Configuration centrale et registre de chemins dynamiques du projet E-commerce AI Scraping.
Fournit des chemins absolus et résolus avec pathlib pour tous les modules.
"""

from __future__ import annotations

import os
from pathlib import Path

# Racine du projet E-commerce AI Scraping
PROJECT_ROOT = Path(__file__).resolve().parent

# 1. Dossier 01_dashboard
DASHBOARD_DIR = PROJECT_ROOT / "01_dashboard"
DASHBOARD_STATIC_DIR = DASHBOARD_DIR / "static"
DASHBOARD_PORT = int(os.environ.get("DASHBOARD_PORT", "8080"))
DASHBOARD_HOST = os.environ.get("DASHBOARD_HOST", "127.0.0.1")

# 2. Dossier 02_scrapers
SCRAPERS_DIR = PROJECT_ROOT / "02_scrapers"
SCRAPERS_CORE_DIR = SCRAPERS_DIR / "core"
SCRAPERS_SOURCES_DIR = SCRAPERS_DIR / "sources"
SCRAPERS_OUTPUTS_DIR = SCRAPERS_DIR / "outputs"

# 3. Dossier 03_ia_normalizer
NORMALIZER_DIR = PROJECT_ROOT / "03_ia_normalizer"
NORMALIZER_ENGINE_DIR = NORMALIZER_DIR / "engine"
NORMALIZER_OUTPUTS_DIR = NORMALIZER_DIR / "outputs"

# 4. Dossier 04_merge_pipeline
MERGE_DIR = PROJECT_ROOT / "04_merge_pipeline"
MERGE_SRC_DIR = MERGE_DIR / "src"
MERGE_OUTPUTS_DIR = MERGE_DIR / "outputs"
MERGE_LATEST_DIR = MERGE_OUTPUTS_DIR / "latest"
MERGE_HISTORY_DIR = MERGE_OUTPUTS_DIR / "history"

# Fichiers maîtres de données unifiées
UNIFIED_DB_PATH = MERGE_LATEST_DIR / "unified_catalog_database.json"
UNIFIED_INDEX_PATH = MERGE_LATEST_DIR / "catalog_index.json"
MASTER_DATASET_CSV_PATH = MERGE_LATEST_DIR / "master_dataset.csv"
DEVICES_DIR = MERGE_LATEST_DIR / "devices"

# Configuration Ollama / LLM
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
DEFAULT_MODEL = os.environ.get("DEFAULT_MODEL", "qwen2.5:3b")
