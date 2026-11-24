"""
Package IA Normalizer pour E-commerce AI V2.
Système asynchrone de normalisation sémantique locale par LLM (Ollama).
"""

from .config import DEFAULT_MODEL, OUTPUT_AI_DIR
from .engine import AsyncAINormalizer

__all__ = ["AsyncAINormalizer", "DEFAULT_MODEL", "OUTPUT_AI_DIR"]
