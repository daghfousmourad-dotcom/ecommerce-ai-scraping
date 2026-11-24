"""
Configuration pour le Système IA de Normalisation Asynchrone (E-commerce AI V2).
"""

from __future__ import annotations
import os
from pathlib import Path

# Chemins des répertoires du projet E-commerce AI Scraping
BASE_DIR = Path(__file__).resolve().parents[2]
INPUT_OUTPUTS_DIR = BASE_DIR / "02_scrapers" / "outputs"
INPUT_DATASET_DIR = INPUT_OUTPUTS_DIR
LISTE_DES_WEB_OUTPUTS_DIR = INPUT_OUTPUTS_DIR
OUTPUT_AI_DIR = BASE_DIR / "03_ia_normalizer" / "outputs"
UNITED_DATASET_DIR = BASE_DIR / "04_merge_pipeline" / "outputs" / "latest"
UNIFIED_DB_PATH = UNITED_DATASET_DIR / "unified_catalog_database.json"
UNIFIED_INDEX_PATH = UNITED_DATASET_DIR / "catalog_index.json"
MERGE_SCRIPT_PATH = BASE_DIR / "04_merge_pipeline" / "src" / "merge_unified_dataset.py"

# Créer les répertoires nécessaires
OUTPUT_AI_DIR.mkdir(parents=True, exist_ok=True)
UNITED_DATASET_DIR.mkdir(parents=True, exist_ok=True)

# Configuration Ollama Local
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
DEFAULT_MODEL = "qwen2.5-coder:7b"
AVAILABLE_MODELS = [
    "qwen3.5:9b",
    "qwen2.5-coder:7b",
    "llama3.1:8b",
    "qwen2.5-coder:14b",
]

# Paramètres d'inférence asynchrone
DEFAULT_CONCURRENCY = 3          # Nombre de requêtes concurrentes à Ollama (optimisé pour puce Apple M4)
DEFAULT_BATCH_SIZE = 3           # Taille de lot optimale (évite les surcharges de contexte sur 9B)
OLLAMA_TIMEOUT_SECONDS = 180     # Timeout sécurisé à 180s (évite les annulations intempestives à 60s)

# Configuration Serveur Web Dashboard
DASHBOARD_PORT = 8080
DASHBOARD_HOST = "127.0.0.1"
