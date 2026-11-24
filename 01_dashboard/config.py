"""
config.py — Configuration dédiée pour le Dashboard Web E-commerce AI V2 (01_dashboard).
Centralise les ports, hôtes, modèles et chemins des données pour l'interface utilisateur.
"""

from __future__ import annotations

import os
from pathlib import Path

# Chemins relatifs à la racine du projet E-commerce AI Scraping
CURRENT_DIR = Path(__file__).resolve().parent
BASE_DIR = CURRENT_DIR.parent

# Dossiers de données
INPUT_OUTPUTS_DIR = BASE_DIR / "02_scrapers" / "outputs"
INPUT_DATASET_DIR = INPUT_OUTPUTS_DIR
LISTE_DES_WEB_OUTPUTS_DIR = INPUT_OUTPUTS_DIR

OUTPUT_AI_DIR = BASE_DIR / "03_ia_normalizer" / "outputs"

UNITED_DATASET_DIR = BASE_DIR / "04_merge_pipeline" / "outputs" / "latest"
UNIFIED_DB_PATH = UNITED_DATASET_DIR / "unified_catalog_database.json"
UNIFIED_INDEX_PATH = UNITED_DATASET_DIR / "catalog_index.json"
MERGE_SCRIPT_PATH = BASE_DIR / "04_merge_pipeline" / "src" / "merge_unified_dataset.py"

# Répertoire statique Web (HTML / CSS / JS)
STATIC_DIR = CURRENT_DIR / "static"

# Configuration Ollama / IA
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
DEFAULT_MODEL = "qwen3.5:9b"
AVAILABLE_MODELS = [
    "qwen3.5:9b",
    "qwen2.5-coder:7b",
    "llama3.1:8b",
    "qwen2.5-coder:14b",
]

DEFAULT_CONCURRENCY = 3
DEFAULT_BATCH_SIZE = 5
OLLAMA_TIMEOUT_SECONDS = 60

# Configuration Serveur Web Dashboard
DASHBOARD_PORT = int(os.environ.get("DASHBOARD_PORT", "8080"))
DASHBOARD_HOST = os.environ.get("DASHBOARD_HOST", "127.0.0.1")
