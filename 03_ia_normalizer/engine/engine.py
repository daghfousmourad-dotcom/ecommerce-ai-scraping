"""
Moteur Asynchrone de Normalisation IA (Ollama / Local LLM) pour E-commerce AI V2.
Traite les données de scraping par batches non-bloquants avec traçabilité et validation.
"""

from __future__ import annotations

import asyncio
import csv
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Coroutine

import aiohttp
import unicodedata

try:
    from scraper.core.parser_utils import clean_text, now_iso_utc
    from scraper.core.row_export import write_jsonl_and_csv
    from scraper.core.run_report import finalize_run_artifacts
except ImportError:
    from core.parser_utils import clean_text, now_iso_utc
    from core.row_export import write_jsonl_and_csv
    from core.run_report import finalize_run_artifacts

try:
    from .config import (
        DEFAULT_BATCH_SIZE,
        DEFAULT_CONCURRENCY,
        DEFAULT_MODEL,
        OLLAMA_HOST,
        OLLAMA_TIMEOUT_SECONDS,
        OUTPUT_AI_DIR,
    )
    from .prompts import PRODUCT_SALE_SYSTEM_PROMPT, REPAIR_PARTS_SYSTEM_PROMPT
    from .device_validator import canonicalize_model_name, is_valid_smartphone_device, extract_specs_from_model
except (ImportError, ValueError):
    from config import (
        DEFAULT_BATCH_SIZE,
        DEFAULT_CONCURRENCY,
        DEFAULT_MODEL,
        OLLAMA_HOST,
        OLLAMA_TIMEOUT_SECONDS,
        OUTPUT_AI_DIR,
    )
    from prompts import PRODUCT_SALE_SYSTEM_PROMPT, REPAIR_PARTS_SYSTEM_PROMPT
    from device_validator import canonicalize_model_name, is_valid_smartphone_device, extract_specs_from_model


def normalize_key(value: str | None) -> str:
    if not value:
        return ""
    v = clean_text(str(value)).lower()
    v = re.sub(r"[^a-z0-9à-ÿ]+", " ", v)
    return re.sub(r"\s+", " ", v).strip()


def make_row_cache_key(row: dict[str, Any]) -> str:
    """Génère une clé de cache strictement unique pour chaque ligne afin d'éviter toute collision inter-modèles."""
    url = str(row.get("source_url") or "")
    brand = str(row.get("raw_brand") or row.get("brand") or "")
    model = str(row.get("raw_model") or row.get("model") or "")
    part_or_title = str(
        row.get("item_name")
        or row.get("product_or_part_name")
        or row.get("item_title")
        or row.get("repair_type_raw")
        or row.get("repair_type")
        or ""
    )
    price = str(row.get("price") or row.get("price_raw") or "")
    return f"{url}::{brand}::{model}::{part_or_title}::{price}"


def make_product_sale_fallback_key(row: dict[str, Any]) -> str:
    """Clé de repli basée sur l'URL, le titre brut et le prix (immuables pour les ventes de produits)."""
    url = str(row.get("source_url") or "")
    title = str(
        row.get("item_title")
        or row.get("product_or_part_name")
        or row.get("item_name")
        or ""
    )
    price = str(row.get("price") or row.get("price_raw") or "")
    return f"PROD::{url}::{title}::{price}"


# ==============================================================================
# TAXONOMIE CANONIQUE DES RÉPARATIONS & CLASSIFICATION SÉCURISÉE HYBRIDE
# ==============================================================================
CANONICAL_REPAIRS_RULES = [
    ("ecran", "screen", re.compile(r"\b(ecran|afficheur|dalle|vitre tactile|lcd|oled|bloc ecran|module afficheur)\b", re.I)),
    ("vitre_arriere", "back_cover", re.compile(r"\b(vitre arriere|facade arriere|coque arriere|dos|cache batterie|cosmetique)\b", re.I)),
    ("batterie", "battery", re.compile(r"\b(batterie|accumulateur|autonomie)\b", re.I)),
    ("connecteur_charge", "charging_port", re.compile(r"\b(connecteur de charge|connecteur charge|connecteur|charge|charging|lightning|usb-c|port usb)\b", re.I)),
    ("camera_arriere", "rear_camera", re.compile(r"\b(camera arriere|appareil photo arriere|lentille camera|camera|photo|lentille)\b", re.I)),
    ("camera_avant", "front_camera", re.compile(r"\b(camera avant|appareil photo avant|selfie|face id|truedepth)\b", re.I)),
    ("haut_parleur", "speaker", re.compile(r"\b(haut-parleur|haut parleur|ecouteur|audio|buzzer|sonnerie|micro|microphone)\b", re.I)),
    ("vibreur", "vibrator", re.compile(r"\b(vibreur|vibrateur|taptic)\b", re.I)),
    ("desoxydation", "desoxydation", re.compile(r"\b(oxydation|desoxydation|decontamination)\b", re.I)),
    ("diagnostic", "diagnostics", re.compile(r"\b(diagnostic|logiciel|software|devis|reinitialisation)\b", re.I)),
]

LLM_TO_CANONICAL_REPAIR = {
    "screen": ("screen", "ecran"),
    "battery": ("battery", "batterie"),
    "back_cover": ("back_cover", "vitre_arriere"),
    "charging_port": ("charging_port", "connecteur_charge"),
    "rear_camera": ("rear_camera", "camera_arriere"),
    "front_camera": ("front_camera", "camera_avant"),
    "speaker": ("speaker", "haut_parleur"),
    "earpiece": ("earpiece", "haut_parleur"),
    "vibrator": ("vibrator", "vibreur"),
    "desoxydation": ("desoxydation", "desoxydation"),
    "diagnostics": ("diagnostics", "diagnostic"),
    "antenna": ("other", "autre"),
    "buttons": ("other", "autre"),
    "motherboard": ("other", "autre"),
    "microphone": ("speaker", "haut_parleur"),
    "other": ("other", "autre"),
}


def resolve_repair_classification(raw_text: str, llm_rep: str | None = None) -> tuple[str, str]:
    """
    Détermine avec certitude (repair_type_en, repair_category_fr) à partir du libellé brut
    et de la suggestion du LLM.
    Retourne toujours un tuple : (repair_type_en, repair_category_fr).
    """
    # 1. Normalisation sans accents
    t_clean = unicodedata.normalize("NFKD", str(raw_text or "").strip().lower())
    t_clean = "".join(c for c in t_clean if not unicodedata.combining(c))

    # 2. Test déterministe prioritaire sur le libellé brut (garantie anti-autre pour réparations évidentes)
    for cat_fr, rep_en, pattern in CANONICAL_REPAIRS_RULES:
        if pattern.search(t_clean):
            return rep_en, cat_fr

    # 3. Prise en compte de la classification LLM si valide
    if llm_rep:
        clean_llm = str(llm_rep).strip().lower()
        if clean_llm in LLM_TO_CANONICAL_REPAIR:
            mapped_en, mapped_fr = LLM_TO_CANONICAL_REPAIR[clean_llm]
            if mapped_fr != "autre":
                return mapped_en, mapped_fr

    return "other", "autre"


# ==============================================================================
# GARDE-FOUS DÉTERMINISTES : VENTES DE SMARTPHONES (Anti-Anomalies & Prix Faux)
# ==============================================================================
REJECT_PACKAGING_RE = re.compile(
    r"\b(bo[iî]te|coffret|packaging|box)\s*(vide|seule?|d'origine\s+vide|empty)\b"
    r"|\b(empty\s+box|box\s+only|bo[iî]te\s+sans\s+t[eé]l[eé]phone)\b",
    re.IGNORECASE,
)

REJECT_SPARE_PARTS_RE = re.compile(
    r"\b(ch[aâ]ssis\s+(seul|central|m[eé]tallique)|cadre\s+m[eé]tallique|pour\s+pi[eè]ces|bloqu[eé]\s+icloud|icloud\s+lock|non\s+fonctionnel|factice|dummy)\b",
    re.IGNORECASE,
)

REJECT_ACCESSORY_ALONE_RE = re.compile(
    r"\b(coque\s+seule|housse\s+seule|[eé]tui\s+seul|verre\s+tremp[eé]\s+seul|option\s+au\s+choix)\b",
    re.IGNORECASE,
)

MODEL_PRICE_FLOORS = [
    (re.compile(r"iphone\s*15", re.IGNORECASE), 250.0),
    (re.compile(r"iphone\s*14", re.IGNORECASE), 180.0),
    (re.compile(r"iphone\s*13", re.IGNORECASE), 140.0),
    (re.compile(r"iphone\s*12", re.IGNORECASE), 100.0),
    (re.compile(r"iphone\s*11", re.IGNORECASE), 80.0),
    (re.compile(r"galaxy\s*s2[45]", re.IGNORECASE), 250.0),
    (re.compile(r"galaxy\s*s23", re.IGNORECASE), 180.0),
    (re.compile(r"pixel\s*(8|9|10)", re.IGNORECASE), 180.0),
]


def is_rejected_sale_item(
    title: str | None,
    brand: str | None,
    model: str | None,
    price: Any,
) -> tuple[bool, str]:
    """Valide scrupuleusement si un article de vente est un vrai smartphone avec un prix cohérent."""
    t = (title or "").strip()
    b = (brand or "").strip()
    m = (model or "").strip()
    full_text = f"{b} {m} {t}".lower()

    if REJECT_PACKAGING_RE.search(full_text):
        return True, "Emballage ou boîte vide sans appareil"

    if REJECT_SPARE_PARTS_RE.search(full_text):
        return True, "Châssis, pièce détachée ou appareil bloqué/HS"

    if REJECT_ACCESSORY_ALONE_RE.search(full_text):
        return True, "Accessoire seul ou annonce multi-options"

    # Planchers de prix universels et par modèle
    if price is not None:
        try:
            p_val = float(price)
            if p_val <= 0:
                return True, f"Prix nul ou négatif ({p_val} €)"
            if p_val < 40.0:
                return True, f"Prix anormalement bas (< 40 € : {p_val} €)"

            for pat, floor in MODEL_PRICE_FLOORS:
                if pat.search(full_text):
                    if p_val < floor:
                        return True, f"Prix impossible pour {m} (< {floor} € : {p_val} €)"
        except (ValueError, TypeError):
            pass

    return False, ""


class AsyncAINormalizer:
    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        concurrency: int = DEFAULT_CONCURRENCY,
        batch_size: int = DEFAULT_BATCH_SIZE,
        ollama_url: str = OLLAMA_HOST,
    ):
        self.model = model
        self.concurrency = concurrency
        self.batch_size = batch_size
        self.ollama_url = ollama_url
        self.is_running = False
        self.should_stop = False

        # Métriques en temps réel
        self.total_rows = 0
        self.processed_rows = 0
        self.cached_rows = 0
        self.delta_rows = 0
        self.modified_rows = 0
        self.filtered_accessories = 0
        self.start_time: float = 0.0
        self.recent_corrections: list[dict[str, Any]] = []
        self.error_count = 0
        self.status_message = "En attente"

    def stop(self) -> None:
        """Arrête le traitement en cours proprement."""
        self.should_stop = True
        self.status_message = "Arrêt demandé..."

    def _get_cache_path(self, source_site: str) -> Path:
        """Chemin du fichier d'index de cache persistant pour une source."""
        return OUTPUT_AI_DIR / f".cache_ai_{source_site}.json"

    def _load_or_bootstrap_cache(self, source_site: str) -> dict[str, dict[str, Any]]:
        """Charge le cache persistant ou le génère depuis les anciens outputs IA certifiés (architecture v2 requise)."""
        cache_path = self._get_cache_path(source_site)
        if cache_path.exists():
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    # Vérifier que le cache est conforme à l'architecture v2 (support réparations et ventes produits)
                    has_v2 = any(
                        isinstance(v, dict) and (
                            v.get("is_dropped")
                            or v.get("domain") == "product_sale"
                            or v.get("fields", {}).get("canonical_slug")
                            or v.get("fields", {}).get("record_status")
                            or v.get("fields", {}).get("model")
                        )
                        for v in data.values()
                    )
                    if has_v2:
                        for v in data.values():
                            if isinstance(v, dict) and not v.get("is_dropped"):
                                flds = v.get("fields", {})
                                if flds.get("repair_type") in ("other", None) or flds.get("repair_category") in ("autre", None):
                                    raw_chk = flds.get("repair_category_raw") or flds.get("raw_repair_label") or ""
                                    c_rep, c_cat = resolve_repair_classification(raw_chk)
                                    if c_cat != "autre":
                                        flds["repair_type"] = c_rep
                                        flds["repair_category"] = c_cat
                                        flds["repair_category_type"] = "CANONICAL"
                                        flds["repair_type_matched"] = True
                        return data
                    # Cache obsolète v1 : ignorer pour appliquer la nouvelle architecture
            except Exception:
                pass

        # Bootstrapping automatique depuis le dernier fichier _ai_refined_*.jsonl valide v2
        cache: dict[str, dict[str, Any]] = {}
        candidates = sorted([
            p for p in OUTPUT_AI_DIR.glob(f"{source_site}_ai_refined_*.jsonl")
            if not p.name.endswith("_issues.jsonl") and not p.name.endswith("_quarantine.jsonl")
        ])
        if candidates:
            latest_refined = candidates[-1]

            # Charger également la quarantaine associée si elle existe
            quarantine_path = latest_refined.parent / f"{latest_refined.stem}_quarantine.jsonl"
            if quarantine_path.exists():
                try:
                    with open(quarantine_path, "r", encoding="utf-8") as qf:
                        for qline in qf:
                            if qline.strip():
                                qobj = json.loads(qline)
                                q_entry = {
                                    "is_dropped": True,
                                    "drop_reason": qobj.get("_drop_reason") or "Accessoire filtré / non-smartphone",
                                }
                                cache[make_row_cache_key(qobj)] = q_entry
                                cache[make_product_sale_fallback_key(qobj)] = q_entry
                except Exception:
                    pass

            try:
                with open(latest_refined, "r", encoding="utf-8") as f:
                    first_line = f.readline()
                    if first_line.strip():
                        first_obj = json.loads(first_line)
                        is_prod_sale = (
                            first_obj.get("data_type") in ("product_sale", "market_price")
                            or "item_title" in first_obj
                        )
                        # Bootstrapping si vente de produit ou métadonnées v2 réparations
                        if is_prod_sale or "canonical_slug" in first_obj or "record_status" in first_obj:
                            f.seek(0)
                            for line in f:
                                if not line.strip():
                                    continue
                                obj = json.loads(line)
                                if is_prod_sale:
                                    if not obj.get("model"):
                                        continue
                                    pkey = make_row_cache_key(obj)
                                    fkey = make_product_sale_fallback_key(obj)
                                    entry = {
                                        "is_dropped": False,
                                        "domain": "product_sale",
                                        "fields": {
                                            "brand": obj.get("brand"),
                                            "raw_brand": obj.get("raw_brand") or obj.get("brand"),
                                            "model": obj.get("model"),
                                            "raw_model": obj.get("raw_model") or obj.get("model"),
                                            "model_normalized": obj.get("model_normalized"),
                                            "device_family": obj.get("device_family"),
                                            "device_scope": obj.get("device_scope", "SMARTPHONE"),
                                            "record_status": obj.get("record_status", "ACCEPTED"),
                                            "storage_gb": obj.get("storage_gb"),
                                            "storage_raw": obj.get("storage_raw"),
                                            "ram_gb": obj.get("ram_gb"),
                                            "color": obj.get("color"),
                                            "condition": obj.get("condition"),
                                            "keep_record": True,
                                        },
                                    }
                                    cache[pkey] = entry
                                    cache[fkey] = entry
                                else:
                                    if not obj.get("canonical_slug") and obj.get("model_status") != "RESOLVED":
                                        continue
                                    key = make_row_cache_key(obj)
                                    fields_dict = {
                                        "brand": obj.get("brand"),
                                        "raw_brand": obj.get("raw_brand"),
                                        "brand_status": obj.get("brand_status"),
                                        "brand_verification_source": obj.get("brand_verification_source"),
                                        "model": obj.get("model"),
                                        "raw_model": obj.get("raw_model"),
                                        "model_candidate": obj.get("model_candidate"),
                                        "model_normalized": obj.get("model_normalized"),
                                        "canonical_slug": obj.get("canonical_slug"),
                                        "slug_type": obj.get("slug_type"),
                                        "resolved_canonical_slug": obj.get("resolved_canonical_slug"),
                                        "provisional_slug": obj.get("provisional_slug"),
                                        "technical_codes": obj.get("technical_codes"),
                                        "normalization_confidence": obj.get("normalization_confidence"),
                                        "resolution_method": obj.get("resolution_method"),
                                        "resolution_strength": obj.get("resolution_strength"),
                                        "model_status": obj.get("model_status"),
                                        "repair_type": obj.get("repair_type"),
                                        "repair_type_matched": obj.get("repair_type_matched"),
                                        "repair_category": obj.get("repair_category"),
                                        "repair_category_raw": obj.get("repair_category_raw"),
                                        "repair_category_type": obj.get("repair_category_type"),
                                        "record_status": obj.get("record_status"),
                                        "device_scope": obj.get("device_scope"),
                                        "device_family": obj.get("device_family"),
                                        "storage_gb": obj.get("storage_gb"),
                                        "storage_raw": obj.get("storage_raw"),
                                        "ram_gb": obj.get("ram_gb"),
                                        "color": obj.get("color"),
                                        "condition": obj.get("condition"),
                                        "keep_record": obj.get("keep_record", True),
                                    }
                                    # Auto-réparation si bootstrap depuis un fichier pollué en other
                                    if fields_dict.get("repair_type") in ("other", None) or fields_dict.get("repair_category") in ("autre", None):
                                        raw_chk = (
                                            obj.get("raw_repair_label")
                                            or obj.get("repair_type_raw")
                                            or obj.get("repair_type")
                                            or obj.get("product_or_part_name")
                                            or obj.get("item_name")
                                            or ""
                                        )
                                        c_rep, c_cat = resolve_repair_classification(raw_chk)
                                        if c_cat != "autre":
                                            fields_dict["repair_type"] = c_rep
                                            fields_dict["repair_category"] = c_cat
                                            fields_dict["repair_category_type"] = "CANONICAL"
                                            fields_dict["repair_type_matched"] = True

                                    cache[key] = {
                                        "is_dropped": False,
                                        "fields": fields_dict,
                                    }
            except Exception:
                pass

        return cache

    def _save_cache(self, source_site: str, cache_data: dict[str, dict[str, Any]]) -> None:
        """Enregistre le cache persistant sur disque."""
        cache_path = self._get_cache_path(source_site)
        try:
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(cache_data, f, ensure_ascii=False)
        except Exception:
            pass

    async def check_ollama_health(self) -> tuple[bool, str]:
        """Vérifie si le service Ollama local est accessible et répond."""
        try:
            timeout = aiohttp.ClientTimeout(total=5)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(f"{self.ollama_url}/api/tags") as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        models = [m.get("name") for m in data.get("models", [])]
                        return True, f"Ollama actif ({len(models)} modèles disponibles)"
                    return False, f"Ollama HTTP {resp.status}"
        except Exception as e:
            return False, f"Connexion Ollama impossible : {e}"

    async def _call_ollama_batch(
        self,
        session: aiohttp.ClientSession,
        batch_items: list[dict[str, Any]],
        system_prompt: str,
        semaphore: asyncio.Semaphore,
    ) -> list[dict[str, Any]]:
        """Appelle Ollama en asynchrone pour un lot d'éléments."""
        async with semaphore:
            if self.should_stop:
                return []

            user_content = json.dumps(batch_items, ensure_ascii=False)
            payload = {
                "model": self.model,
                "format": "json",
                "stream": False,
                "options": {
                    "temperature": 0.0,
                    "num_ctx": 4096,
                },
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
            }

            timeout = aiohttp.ClientTimeout(total=OLLAMA_TIMEOUT_SECONDS)
            for attempt in range(2):
                try:
                    async with session.post(
                        f"{self.ollama_url}/api/chat",
                        json=payload,
                        timeout=timeout,
                    ) as resp:
                        if resp.status == 200:
                            res_json = await resp.json()
                            content = res_json.get("message", {}).get("content", "{}")
                            parsed = json.loads(content)
                            if isinstance(parsed, dict) and "results" in parsed:
                                return parsed["results"]
                            elif isinstance(parsed, list):
                                return parsed
                        else:
                            self.error_count += 1
                            if resp.status == 404:
                                self.status_message = f"Erreur Ollama 404 : modèle '{self.model}' introuvable."
                        await asyncio.sleep(1)
                except Exception as e:
                    if attempt == 1:
                        self.error_count += 1
                    await asyncio.sleep(1)

            return []

    async def normalize_file(
        self,
        input_file: str | Path,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        incremental: bool = True,
    ) -> dict[str, Any]:
        """Exécute la normalisation asynchrone d'un fichier (avec support du mode incrémental delta)."""
        self.is_running = True
        self.should_stop = False
        self.processed_rows = 0
        self.cached_rows = 0
        self.delta_rows = 0
        self.modified_rows = 0
        self.filtered_accessories = 0
        self.error_count = 0
        self.recent_corrections = []
        self.start_time = time.time()
        self.status_message = "Initialisation..."

        in_path = Path(input_file)
        if not in_path.exists():
            raise FileNotFoundError(f"Fichier introuvable : {in_path}")

        # Validation dynamique du modèle local Ollama pour éviter les 404 silencieux
        try:
            timeout_chk = aiohttp.ClientTimeout(total=4)
            async with aiohttp.ClientSession(timeout=timeout_chk) as chk_sess:
                async with chk_sess.get(f"{self.ollama_url}/api/tags") as tags_resp:
                    if tags_resp.status == 200:
                        tags_data = await tags_resp.json()
                        installed_models = [m.get("name", "") for m in tags_data.get("models", [])]
                        if installed_models and self.model not in installed_models:
                            matched = next((m for m in installed_models if self.model in m or m in self.model), None)
                            if not matched:
                                matched = next((m for m in ["qwen2.5-coder:7b", "llama3.1:8b", "qwen3.5:9b", "qwen2.5-coder:14b"] if m in installed_models), installed_models[0])
                            self.model = matched
        except Exception:
            pass

        # 1. Charger les données d'entrée (CSV ou JSON)
        rows: list[dict[str, Any]] = []
        if in_path.suffix.lower() == ".csv":
            with open(in_path, "r", encoding="utf-8", errors="ignore") as f:
                reader = csv.DictReader(f)
                rows = list(reader)
        elif in_path.suffix.lower() == ".json":
            with open(in_path, "r", encoding="utf-8", errors="ignore") as f:
                data = json.load(f)
                rows = data if isinstance(data, list) else data.get("rows", [])

        self.total_rows = len(rows)
        if not rows:
            self.is_running = False
            return {"status": "empty", "rows_count": 0}

        # 2. Déterminer le domaine métier (Vente de Produits vs Réparations)
        first_row = rows[0]
        is_product_sale = "item_title" in first_row or first_row.get("data_type") in ("market_price", "product_sale")
        system_prompt = PRODUCT_SALE_SYSTEM_PROMPT if is_product_sale else REPAIR_PARTS_SYSTEM_PROMPT
        if "targets" in in_path.stem:
            source_site = "targets"
        elif in_path.stem.startswith("phone_cbd") or in_path.stem.startswith("db_phone_cbd"):
            source_site = "phone_cbd"
        elif in_path.stem.startswith("db_"):
            source_site = in_path.stem.replace("db_", "").split(".")[0]
        else:
            source_site = first_row.get("source_site") or in_path.stem.split("_")[0]
        scrape_started_at = first_row.get("scrape_started_at") or now_iso_utc()

        # 3. Préparer les données et le cache incrémental
        refined_rows: list[dict[str, Any]] = [dict(r) for r in rows]
        cache: dict[str, dict[str, Any]] = self._load_or_bootstrap_cache(source_site) if incremental else {}
        delta_items: list[tuple[int, dict[str, Any]]] = []

        for idx, row in enumerate(refined_rows):
            key = make_row_cache_key(row)
            fkey = make_product_sale_fallback_key(row) if is_product_sale else ""
            cache_entry = cache.get(key) or (cache.get(fkey) if fkey else None)
            if incremental and cache_entry is not None:
                if cache_entry.get("is_dropped"):
                    row["_is_dropped_accessory"] = True
                    row["_drop_reason"] = cache_entry.get("drop_reason") or "Accessoire filtré"
                    self.filtered_accessories += 1
                else:
                    fields = cache_entry.get("fields", {})
                    # Sécurité auto-réparation pour réparations classées à tort en other/autre
                    if not is_product_sale and (fields.get("repair_type") in ("other", None) or fields.get("repair_category") in ("autre", None)):
                        raw_chk = (
                            row.get("raw_repair_label")
                            or row.get("repair_type_raw")
                            or row.get("repair_type")
                            or row.get("product_or_part_name")
                            or row.get("item_name")
                            or ""
                        )
                        c_rep, c_cat = resolve_repair_classification(raw_chk)
                        if c_cat != "autre":
                            fields["repair_type"] = c_rep
                            fields["repair_category"] = c_cat
                            fields["repair_category_type"] = "CANONICAL"
                            fields["repair_type_matched"] = True
                    for field_k, field_v in fields.items():
                        if field_v is not None:
                            row[field_k] = field_v
                    self.modified_rows += 1
                self.cached_rows += 1
                self.processed_rows += 1
            else:
                delta_items.append((idx, row))

        self.delta_rows = len(delta_items)

        # Rapport initial immédiat pour l'utilisateur
        if self.cached_rows > 0:
            self.status_message = (
                f"⚡ Mode Incrémental : {self.cached_rows} lignes certifiées issues du cache. "
                f"Traitement LLM de {self.delta_rows} lignes inédites..."
            )
            if progress_callback:
                progress_callback({
                    "total_rows": self.total_rows,
                    "processed_rows": self.processed_rows,
                    "cached_rows": self.cached_rows,
                    "delta_rows": self.delta_rows,
                    "modified_rows": self.modified_rows,
                    "filtered_accessories": self.filtered_accessories,
                    "speed_rows_per_sec": 0.0,
                    "progress_percent": round((self.processed_rows / self.total_rows) * 100, 1),
                    "status_message": self.status_message,
                    "recent_corrections": [],
                })
        else:
            self.status_message = f"Normalisation complète ({'Vente Produits' if is_product_sale else 'Réparations'})..."

        # 4. Préparer les lots asynchrones uniquement sur delta_items
        batches: list[list[tuple[int, dict[str, Any]]]] = []
        for i in range(0, len(delta_items), self.batch_size):
            batches.append(delta_items[i : i + self.batch_size])

        semaphore = asyncio.Semaphore(self.concurrency)

        async with aiohttp.ClientSession() as session:
            for batch in batches:
                if self.should_stop:
                    break

                batch_payload = []
                for idx, row in batch:
                    if is_product_sale:
                        batch_payload.append({
                            "id": idx,
                            "item_title": row.get("item_title", ""),
                        })
                    else:
                        batch_payload.append({
                            "id": idx,
                            "product_or_part_name": row.get("product_or_part_name", ""),
                            "repair_type_raw": row.get("repair_type_raw", ""),
                            "brand": row.get("brand", ""),
                            "model": row.get("model", ""),
                        })

                results = await self._call_ollama_batch(session, batch_payload, system_prompt, semaphore)

                # Fusionner les résultats du lot
                result_map = {res.get("id"): res for res in results if isinstance(res, dict) and "id" in res}
                for idx, orig_row in batch:
                    self.processed_rows += 1
                    row_key = make_row_cache_key(orig_row)
                    corr = result_map.get(idx) or {}
                    was_modified = False
                    before_snapshot = {
                        "brand": orig_row.get("brand"),
                        "model": orig_row.get("model"),
                        "storage_gb": orig_row.get("storage_gb"),
                        "repair_type": orig_row.get("repair_type"),
                    }

                    if is_product_sale:
                        if "raw_model" not in orig_row:
                            orig_row["raw_model"] = orig_row.get("model") or ""
                        if "raw_brand" not in orig_row:
                            orig_row["raw_brand"] = orig_row.get("brand") or ""

                        raw_title = orig_row.get("item_title") or ""
                        raw_price = orig_row.get("price")
                        eff_brand = corr.get("brand") or orig_row.get("raw_brand") or orig_row.get("brand") or ""
                        eff_model = corr.get("model") or orig_row.get("raw_model") or orig_row.get("model") or ""

                        # Garde-fou déterministe absolu (boîtes vides, accessoires, planchers de prix)
                        is_rejected, reject_reason = is_rejected_sale_item(raw_title, eff_brand, eff_model, raw_price)
                        is_device = corr.get("is_valid_device", True)

                        if is_rejected or not is_device:
                            orig_row["_is_dropped_accessory"] = True
                            orig_row["_drop_reason"] = reject_reason or "Non-smartphone / accessoire"
                            self.filtered_accessories += 1
                            was_modified = True
                            if incremental:
                                drop_entry = {"is_dropped": True, "drop_reason": orig_row["_drop_reason"]}
                                cache[row_key] = drop_entry
                                cache[make_product_sale_fallback_key(orig_row)] = drop_entry
                            continue

                        # Mise à jour des entités nettoyées
                        if corr.get("brand") and corr["brand"] != orig_row.get("brand"):
                            orig_row["brand"] = corr["brand"]
                            orig_row["device_family"] = corr["brand"]
                            was_modified = True

                        if corr.get("model"):
                            raw_corrected_model = corr["model"]
                            # Extraction et dépollution complète modèle + stockage + couleur
                            canonical_model, ext_storage, ext_color = extract_specs_from_model(eff_brand, raw_corrected_model)
                            if canonical_model is None:
                                # Modèle invalide (multi-modèle, bundle, code interne, specs pures) -> rejet
                                orig_row["_is_dropped_accessory"] = True
                                orig_row["_drop_reason"] = "Modèle invalide / bundle"
                                self.filtered_accessories += 1
                                was_modified = True
                                if incremental:
                                    drop_entry = {"is_dropped": True, "drop_reason": orig_row["_drop_reason"]}
                                    cache[row_key] = drop_entry
                                    cache[make_product_sale_fallback_key(orig_row)] = drop_entry
                                continue
                            if canonical_model != orig_row.get("model"):
                                orig_row["model"] = canonical_model
                                orig_row["model_normalized"] = normalize_key(canonical_model)
                                was_modified = True

                            # Récupération sécurisée du stockage si masqué ou confondu avec la RAM
                            if ext_storage and (corr.get("storage_gb") is None or int(corr.get("storage_gb", 0)) in (1, 2, 3, 4, 6, 8, 12, 16)):
                                corr["storage_gb"] = ext_storage

                            # Récupération de la couleur si absente du LLM
                            if ext_color and not corr.get("color"):
                                corr["color"] = ext_color

                        if corr.get("storage_gb") is not None:
                            try:
                                s_val = int(corr["storage_gb"])
                                if orig_row.get("storage_gb") != s_val:
                                    orig_row["storage_gb"] = s_val
                                    orig_row["storage_raw"] = f"{s_val} Go"
                                    was_modified = True
                            except (ValueError, TypeError):
                                pass

                        if corr.get("ram_gb") is not None:
                            try:
                                r_val = int(corr["ram_gb"])
                                if orig_row.get("ram_gb") != r_val:
                                    orig_row["ram_gb"] = r_val
                                    was_modified = True
                            except (ValueError, TypeError):
                                pass

                        if corr.get("color") and orig_row.get("color") != corr["color"]:
                            orig_row["color"] = corr["color"]
                            was_modified = True

                        if corr.get("condition"):
                            cond_val = "refurbished" if "refurbish" in str(corr["condition"]).lower() else "new"
                            if orig_row.get("condition") != cond_val:
                                orig_row["condition"] = cond_val
                                was_modified = True

                        orig_row["record_status"] = "ACCEPTED"
                        orig_row["device_scope"] = "SMARTPHONE"
                        orig_row["keep_record"] = True

                        if was_modified:
                            self.modified_rows += 1

                        if incremental:
                            sale_entry = {
                                "is_dropped": False,
                                "domain": "product_sale",
                                "fields": {
                                    "brand": orig_row.get("brand"),
                                    "raw_brand": orig_row.get("raw_brand"),
                                    "model": orig_row.get("model"),
                                    "raw_model": orig_row.get("raw_model"),
                                    "model_normalized": orig_row.get("model_normalized"),
                                    "device_family": orig_row.get("device_family"),
                                    "device_scope": orig_row.get("device_scope"),
                                    "record_status": orig_row.get("record_status"),
                                    "storage_gb": orig_row.get("storage_gb"),
                                    "storage_raw": orig_row.get("storage_raw"),
                                    "ram_gb": orig_row.get("ram_gb"),
                                    "color": orig_row.get("color"),
                                    "condition": orig_row.get("condition"),
                                    "keep_record": True,
                                },
                            }
                            cache[row_key] = sale_entry
                            cache[make_product_sale_fallback_key(orig_row)] = sale_entry

                        if len(self.recent_corrections) < 20:
                            self.recent_corrections.insert(0, {
                                "original_title": orig_row.get("item_title") or orig_row.get("product_or_part_name"),
                                "is_product_sale": True,
                                "before": before_snapshot,
                                "after": {
                                    "brand": orig_row.get("brand"),
                                    "model": orig_row.get("model"),
                                    "storage_gb": orig_row.get("storage_gb"),
                                    "repair_type": None,
                                    "condition": orig_row.get("condition"),
                                },
                            })

                    else:
                        # Domaine réparations : filtrage sécurisé des consommables / outils purs d'atelier
                        part_title = (orig_row.get("product_or_part_name") or orig_row.get("item_title") or "").lower()
                        consumable_keywords = [
                            "adhésif", "adhesif", "boite de", "boîte de", "lot de", "kit de vis",
                            "vis pour", "extracteur sim", "tournevis", "spatule", "colle",
                            "chiffon", "verre trempé", "verre trempe", "film hydrogel", "coque de protection"
                        ]
                        is_explicit_consumable = any(kw in part_title for kw in consumable_keywords)
                        if is_explicit_consumable:
                            orig_row["record_status"] = "QUARANTINE"
                            orig_row["device_scope"] = "NON_SMARTPHONE"
                            orig_row["keep_record"] = True
                            orig_row["_is_dropped_accessory"] = True
                            orig_row["_drop_reason"] = "Consommable / outil d'atelier (non-réparation)"
                            self.filtered_accessories += 1
                            was_modified = True
                            if incremental:
                                cache[row_key] = {"is_dropped": True, "drop_reason": orig_row["_drop_reason"]}
                            continue

                        # 1. Préservation intégrale des données brutes
                        if "raw_model" not in orig_row:
                            orig_row["raw_model"] = orig_row.get("model") or ""
                        if "raw_brand" not in orig_row:
                            orig_row["raw_brand"] = orig_row.get("brand") or ""
                        if "raw_repair_label" not in orig_row:
                            orig_row["raw_repair_label"] = orig_row.get("repair_type_raw") or orig_row.get("product_or_part_name") or ""

                        # 2. Vérification intelligente de la marque (effectuée dans la phase IA)
                        try:
                            from .model_normalizer import BrandVerifier, normalize_device_entry
                        except (ImportError, ValueError):
                            from model_normalizer import BrandVerifier, normalize_device_entry
                        b_verifier = BrandVerifier.get_instance()
                        eff_brand = corr.get("brand") or orig_row.get("raw_brand") or orig_row.get("brand") or ""
                        eff_model_raw = corr.get("model") or orig_row.get("raw_model") or orig_row.get("model") or ""
                        eff_title = orig_row.get("product_or_part_name") or orig_row.get("item_title") or ""

                        b_res = await b_verifier.verify_brand(eff_brand, eff_model_raw, eff_title, session=session)
                        orig_row["brand"] = b_res.canonical_brand
                        orig_row["brand_status"] = b_res.status
                        orig_row["brand_verification_source"] = b_res.verification_source

                        # Si la marque est formellement NOT_SMARTPHONE (ex: Nikon, Nintendo, Casio), exclusion vers la quarantaine
                        if b_res.status == "NOT_SMARTPHONE":
                            orig_row["record_status"] = "QUARANTINE"
                            orig_row["device_scope"] = "NON_SMARTPHONE"
                            orig_row["keep_record"] = True
                            orig_row["_is_dropped_accessory"] = True
                            orig_row["_drop_reason"] = f"Marque non-smartphone : {b_res.canonical_brand}"
                            self.filtered_accessories += 1
                            was_modified = True
                            if incremental:
                                cache[row_key] = {"is_dropped": True, "drop_reason": orig_row["_drop_reason"]}
                            continue

                        # Vérification de l'appareil (rejet des PC portables, tablettes pures, appareils photo)
                        is_sp, sp_reason = is_valid_smartphone_device(b_res.canonical_brand, eff_model_raw, eff_title)
                        if not is_sp:
                            orig_row["record_status"] = "QUARANTINE"
                            orig_row["device_scope"] = "NON_SMARTPHONE"
                            orig_row["keep_record"] = True
                            orig_row["_is_dropped_accessory"] = True
                            orig_row["_drop_reason"] = sp_reason
                            self.filtered_accessories += 1
                            was_modified = True
                            if incremental:
                                cache[row_key] = {"is_dropped": True, "drop_reason": orig_row["_drop_reason"]}
                            continue

                        # 3. Normalisation contextuelle du modèle en 3 niveaux (segments + codes techniques + slug canonique)
                        norm_res = normalize_device_entry(eff_model_raw, raw_brand=b_res.canonical_brand, item_title=eff_title)
                        orig_row["model"] = norm_res["model"]
                        orig_row["model_candidate"] = norm_res["model_candidate"]
                        orig_row["model_normalized"] = normalize_key(norm_res["model"])
                        orig_row["canonical_slug"] = norm_res["canonical_slug"]
                        orig_row["canonical_resolution"] = norm_res.get("canonical_resolution", False)
                        orig_row["slug_type"] = norm_res.get("slug_type", "provisional")
                        orig_row["resolved_canonical_slug"] = norm_res.get("resolved_canonical_slug")
                        orig_row["provisional_slug"] = norm_res.get("provisional_slug")
                        orig_row["technical_codes"] = norm_res["technical_codes"]
                        orig_row["normalization_confidence"] = norm_res["normalization_confidence"]
                        orig_row["resolution_method"] = norm_res["resolution_method"]
                        orig_row["resolution_strength"] = norm_res.get("resolution_strength", "MEDIUM")
                        orig_row["model_status"] = norm_res.get("model_status", "RESOLVED" if norm_res["normalization_confidence"] >= 0.90 else "UNRESOLVED")

                        # 4. Catégorisation additive et sécurisée (hybride IA + déterministe)
                        raw_rep = (
                            orig_row.get("raw_repair_label")
                            or orig_row.get("repair_type_raw")
                            or orig_row.get("repair_type")
                            or orig_row.get("product_or_part_name")
                            or orig_row.get("item_name")
                            or ""
                        )
                        llm_rep = corr.get("repair_type")
                        corr_rep, cat_fr = resolve_repair_classification(raw_rep, llm_rep)

                        orig_row["repair_type"] = corr_rep
                        orig_row["repair_category"] = cat_fr
                        orig_row["repair_category_raw"] = raw_rep
                        orig_row["repair_category_type"] = "CANONICAL" if cat_fr != "autre" else "AUTRE"
                        orig_row["repair_type_matched"] = (cat_fr != "autre")

                        # 5. Déclaration de statut global unique
                        orig_row["record_status"] = "ACCEPTED"
                        orig_row["device_scope"] = "SMARTPHONE"
                        orig_row["keep_record"] = True
                        was_modified = True

                        if was_modified:
                            self.modified_rows += 1
                            if incremental:
                                cache[row_key] = {
                                    "is_dropped": False,
                                    "fields": {
                                        "brand": orig_row.get("brand"),
                                        "raw_brand": orig_row.get("raw_brand"),
                                        "brand_status": orig_row.get("brand_status"),
                                        "brand_verification_source": orig_row.get("brand_verification_source"),
                                        "model": orig_row.get("model"),
                                        "raw_model": orig_row.get("raw_model"),
                                        "model_candidate": orig_row.get("model_candidate"),
                                        "model_normalized": orig_row.get("model_normalized"),
                                        "canonical_slug": orig_row.get("canonical_slug"),
                                        "technical_codes": orig_row.get("technical_codes"),
                                        "normalization_confidence": orig_row.get("normalization_confidence"),
                                        "resolution_method": orig_row.get("resolution_method"),
                                        "resolution_strength": orig_row.get("resolution_strength"),
                                        "model_status": orig_row.get("model_status"),
                                        "repair_type": orig_row.get("repair_type"),
                                        "repair_type_matched": orig_row.get("repair_type_matched"),
                                        "repair_category": orig_row.get("repair_category"),
                                        "repair_category_raw": orig_row.get("repair_category_raw"),
                                        "device_family": orig_row.get("device_family"),
                                        "storage_gb": orig_row.get("storage_gb"),
                                        "storage_raw": orig_row.get("storage_raw"),
                                        "ram_gb": orig_row.get("ram_gb"),
                                        "color": orig_row.get("color"),
                                        "condition": orig_row.get("condition"),
                                        "keep_record": orig_row.get("keep_record", True),
                                    },
                                }
                            if len(self.recent_corrections) < 20:
                                self.recent_corrections.insert(0, {
                                    "original_title": orig_row.get("product_or_part_name") or orig_row.get("item_title"),
                                    "is_product_sale": is_product_sale,
                                    "before": before_snapshot,
                                    "after": {
                                        "brand": orig_row.get("brand"),
                                        "model": orig_row.get("model"),
                                        "storage_gb": orig_row.get("storage_gb"),
                                        "repair_type": orig_row.get("repair_type"),
                                        "condition": orig_row.get("condition"),
                                    },
                                })
                        else:
                            if incremental:
                                cache[row_key] = {
                                    "is_dropped": False,
                                    "fields": {
                                        "brand": orig_row.get("brand"),
                                        "raw_brand": orig_row.get("raw_brand"),
                                        "brand_status": orig_row.get("brand_status"),
                                        "brand_verification_source": orig_row.get("brand_verification_source"),
                                        "model": orig_row.get("model"),
                                        "raw_model": orig_row.get("raw_model"),
                                        "model_candidate": orig_row.get("model_candidate"),
                                        "model_normalized": orig_row.get("model_normalized"),
                                        "canonical_slug": orig_row.get("canonical_slug"),
                                        "technical_codes": orig_row.get("technical_codes"),
                                        "normalization_confidence": orig_row.get("normalization_confidence"),
                                        "resolution_method": orig_row.get("resolution_method"),
                                        "resolution_strength": orig_row.get("resolution_strength"),
                                        "model_status": orig_row.get("model_status"),
                                        "repair_type": orig_row.get("repair_type"),
                                        "repair_type_matched": orig_row.get("repair_type_matched"),
                                        "repair_category": orig_row.get("repair_category"),
                                        "repair_category_raw": orig_row.get("repair_category_raw"),
                                        "device_family": orig_row.get("device_family"),
                                        "storage_gb": orig_row.get("storage_gb"),
                                        "storage_raw": orig_row.get("storage_raw"),
                                        "ram_gb": orig_row.get("ram_gb"),
                                        "color": orig_row.get("color"),
                                        "condition": orig_row.get("condition"),
                                        "keep_record": orig_row.get("keep_record", True),
                                    },
                                }

                # Sauvegarde intermédiaire du cache toutes les 25 lignes delta traitées
                if incremental and (self.processed_rows - self.cached_rows) % 25 == 0:
                    self._save_cache(source_site, cache)

                # Callback de progression
                if progress_callback:
                    elapsed = max(0.1, time.time() - self.start_time)
                    active_speed = round((self.processed_rows - self.cached_rows) / elapsed, 1) if elapsed > 0 else 0.0
                    progress_callback({
                        "total_rows": self.total_rows,
                        "processed_rows": self.processed_rows,
                        "cached_rows": self.cached_rows,
                        "delta_rows": self.delta_rows,
                        "modified_rows": self.modified_rows,
                        "filtered_accessories": self.filtered_accessories,
                        "speed_rows_per_sec": active_speed,
                        "progress_percent": round((self.processed_rows / self.total_rows) * 100, 1),
                        "status_message": self.status_message,
                        "recent_corrections": self.recent_corrections[:5],
                    })

        # 5. Sauvegarder le cache mis à jour
        if incremental:
            self._save_cache(source_site, cache)

        # 6. Isoler les données exclues vers la quarantaine (Principe de Rétention / Zéro Perte)
        final_rows = [r for r in refined_rows if not r.get("_is_dropped_accessory", False)]
        quarantine_rows = [r for r in refined_rows if r.get("_is_dropped_accessory", False)]

        # 7. Exporter les fichiers d'artefacts E-commerce AI V2 dans scraper/outputs_ai/
        time_suffix = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S0000")
        out_name_base = f"{source_site}_ai_refined_{time_suffix}"

        csv_path = OUTPUT_AI_DIR / f"{out_name_base}.csv"
        jsonl_path = OUTPUT_AI_DIR / f"{out_name_base}.jsonl"
        json_bundle = OUTPUT_AI_DIR / f"{out_name_base}.json"
        run_json = OUTPUT_AI_DIR / f"{out_name_base}_run.json"
        issues_jsonl = OUTPUT_AI_DIR / f"{out_name_base}_issues.jsonl"
        quarantine_jsonl = OUTPUT_AI_DIR / f"{out_name_base}_quarantine.jsonl"

        if quarantine_rows:
            with open(quarantine_jsonl, "w", encoding="utf-8") as qfp:
                for q_row in quarantine_rows:
                    qfp.write(json.dumps(q_row, ensure_ascii=False) + "\n")

        # Nettoyage des champs temporaires internes
        for r in final_rows:
            r.pop("_is_dropped_accessory", None)
            r.pop("was_modified", None)
            for temp_k in [k for k in r.keys() if k.startswith("_")]:
                r.pop(temp_k, None)

        write_jsonl_and_csv(final_rows, jsonl_path=jsonl_path, csv_path=csv_path)

        with open(json_bundle, "w", encoding="utf-8") as fp:
            json.dump(
                {
                    "source_site": source_site,
                    "scrape_started_at": scrape_started_at,
                    "ai_refined_at": now_iso_utc(),
                    "ai_model": self.model,
                    "row_count": len(final_rows),
                    "rows": final_rows,
                },
                fp,
                indent=2,
                ensure_ascii=False,
            )

        run_final_status = "completed" if (not self.should_stop and self.processed_rows >= self.total_rows) else "partial"
        finalize_run_artifacts(
            source_site=source_site,
            scrape_started_at=scrape_started_at,
            json_path=json_bundle,
            jsonl_path=jsonl_path,
            csv_path=csv_path,
            row_count=len(final_rows) if not self.should_stop else self.processed_rows,
            apply_cleaning=True,
            clean_report=None,
            fetch_errors=0,
            issues=[],
            status=run_final_status,
        )

        self.is_running = False
        self.status_message = "Terminé avec succès !"

        return {
            "status": "completed" if not self.should_stop else "stopped",
            "source_site": source_site,
            "total_rows": self.total_rows,
            "cached_rows": self.cached_rows,
            "delta_rows": self.delta_rows,
            "output_rows": len(final_rows),
            "modified_rows": self.modified_rows,
            "filtered_accessories": self.filtered_accessories,
            "paths": {
                "csv": str(csv_path),
                "json": str(json_bundle),
                "jsonl": str(jsonl_path),
                "run_json": str(run_json),
                "issues_jsonl": str(issues_jsonl),
            },
        }
