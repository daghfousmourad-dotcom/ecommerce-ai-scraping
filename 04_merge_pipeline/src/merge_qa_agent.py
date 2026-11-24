#!/usr/bin/env python3
"""
merge_qa_agent.py — Agent de Vérification & Réparation Automatique du Merge E-commerce AI Scraping

Cet agent s'exécute APRÈS chaque merge (merge_unified_dataset.py) et :
1. Analyse chaque device du dataset unifié selon 8 règles de détection
2. Applique des réparations automatiques (suppression, correction de champs)
3. Génère un rapport JSON + affichage console

Usage :
    python merge_qa_agent.py [--auto-repair] [--dataset-dir <path>]

Options :
    --auto-repair    Applique automatiquement les réparations suggérées
    --dataset-dir    Chemin vers le dossier devices/ (optionnel)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ─────────────────────────────────────────────────────────────────────
# 0. CONFIG
# ─────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
UNITED_DATASET_DIR = BASE_DIR / "united_dataset"
DEVICES_DIR = UNITED_DATASET_DIR / "devices"
UNIFIED_DB_FILE = UNITED_DATASET_DIR / "unified_catalog_database.json"
QA_REPORTS_DIR = UNITED_DATASET_DIR / "qa_reports"
QA_REPORTS_DIR.mkdir(parents=True, exist_ok=True)

# ─────────────────────────────────────────────────────────────────────
# 1. PATTERNS DE DÉTECTION
# ─────────────────────────────────────────────────────────────────────
PSEUDO_DEVICE_KEYWORDS = re.compile(
    r"\b(batterie|ecran|[eé]cran|vitre|reparation|r[ée]paration|connecteur|charge|devis|"
    r"diagnostic|haut.parleur|micro|camera|cam[eé]ra|vibreur|"
    r"desoxydation|d[eé]soxydation|desox|flash|nappe|hp)\b",
    re.IGNORECASE,
)

PSEUDO_PART_NAMES = re.compile(
    r"^(cam[eé]ra\s+(arri[eèé]re|frontale|avant)|haut[\s-]parleur|batterie|connecteur(\s+de)?\s+charge|vitre(\s+arri[eèé]re)?|[eé]cran|micro|vibreur|d[eé]soxydation)$",
    re.IGNORECASE,
)

MULTI_DEVICE_GROUP_RE = re.compile(
    r"([a-zA-Z0-9]+\s*/\s*[a-zA-Z0-9]+\s*/|redmi.*poco|poco.*redmi|\b9a/9c\b|razr.*razr|\b70 lite/x8\b|\bg17/g17\b|\b200 lite/honor\b)",
    re.IGNORECASE,
)

MARKETING_SPAM_RE = re.compile(
    r"\b(agentic ai|ia agentique|nightography|pantalla amoled|[eé]cran amoled.*120\s*hz|"
    r"verrouill[eé] par carte sim|ohne sim[- ]lock|d[eé]bloqu[eé] avec (gemini|module photo)|"
    r"triple appareil photo|wide oled.*camera|triple objectif avec nouve|"
    r"affichage dynamique amoled|pulgadas|autonomie \d+gb)\b",
    re.IGNORECASE,
)

NON_SMARTPHONE_APPLE = re.compile(
    r"\b(ipad|macbook|imac|apple.watch|watch.series|watch.ultra|airpods|watch.se)\b",
    re.IGNORECASE,
)

INVALID_MODEL_PATTERNS = re.compile(
    r"(\d+\s*/\s*\d+|\d+\s*-\s*\d+\s*(series|s[eé]rie)|\bi17\b|ip\d{4}|ippro\d+|\+\s*(chargeur|coque|cable|airpods))",
    re.IGNORECASE,
)

RECENT_MODEL_RE = re.compile(
    r"\b(202[2-9]|20[3-9]\d|galaxy\s+s2[2-9]|galaxy\s+s3\d|iphone\s+1[3-9]|iphone\s+2\d|pixel\s+[7-9]|pixel\s+1\d)\b",
    re.IGNORECASE,
)

IPHONE_SE_VARIANT_RE = re.compile(r"apple_iphone_se", re.IGNORECASE)

SALE_PRICE_FLOOR = 40.0

SEV_CRIT = "CRITIQUE"
SEV_WARN = "WARNING"
SEV_INFO = "INFO"

# ─────────────────────────────────────────────────────────────────────
# 2. DÉTECTION
# ─────────────────────────────────────────────────────────────────────
def detect_anomalies(device_id: str, doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    anomalies = []
    brand = doc.get("brand", "") or ""
    model = (doc.get("model", "") or "").strip()
    storage = doc.get("storage")
    ram = doc.get("ram_gb")
    repairs = doc.get("repairs", {})
    market_sales = doc.get("market_sales", {})
    dev_id_text = device_id.replace("_", " ")

    # R1 — PSEUDO-DEVICE (nom de pièce comme nom de téléphone ou slug de réparation pure)
    if PSEUDO_PART_NAMES.search(model) or (
        PSEUDO_DEVICE_KEYWORDS.search(dev_id_text)
        and not any(k in dev_id_text.lower() for k in ["galaxy", "iphone", "pixel", "xiaomi", "redmi", "honor", "sony", "huawei", "oppo", "vivo", "motorola", "oneplus", "nothing"])
    ):
        anomalies.append({
            "rule": "PSEUDO_DEVICE", "severity": SEV_CRIT,
            "message": f"device_id '{device_id}' ou modèle '{model}' correspond à une pièce de réparation — pseudo-device",
            "suggested_action": "DELETE",
        })

    # R2 — MODÈLE INVALIDE OU MULTI-APPAREILS GROUPÉS
    if model and (INVALID_MODEL_PATTERNS.search(model) or MULTI_DEVICE_GROUP_RE.search(model) or MULTI_DEVICE_GROUP_RE.search(dev_id_text)):
        anomalies.append({
            "rule": "INVALID_MODEL", "severity": SEV_CRIT,
            "message": f"Nom de modèle invalide ou appareils multiples groupés : '{model}'",
            "suggested_action": "DELETE",
        })

    # R2b — TITRE MARKETING / FICHE TECHNIQUE POLLUTION
    if model and (MARKETING_SPAM_RE.search(model) or MARKETING_SPAM_RE.search(dev_id_text)):
        anomalies.append({
            "rule": "MARKETING_SPAM_TITLE", "severity": SEV_CRIT,
            "message": f"Titre marketing / fiche technique brute : '{model}'",
            "suggested_action": "DELETE",
        })

    # R3 — NON-SMARTPHONE APPLE
    if brand.lower() == "apple" and NON_SMARTPHONE_APPLE.search(model):
        anomalies.append({
            "rule": "NON_SMARTPHONE", "severity": SEV_CRIT,
            "message": f"Produit Apple non-smartphone : '{model}'",
            "suggested_action": "DELETE",
        })

    # R4 — PRIX BAS
    for cond in ("new", "refurbished", "used"):
        min_price = market_sales.get(cond, {}).get("summary", {}).get("min_price")
        if min_price is not None and min_price < SALE_PRICE_FLOOR:
            anomalies.append({
                "rule": "LOW_PRICE", "severity": SEV_CRIT,
                "message": f"Prix {cond} anormalement bas : {min_price}€ < {SALE_PRICE_FLOOR}€",
                "suggested_action": "REMOVE_LOW_OFFERS",
            })

    # R5 — MARQUE MANQUANTE
    if not brand.strip():
        anomalies.append({
            "rule": "MISSING_BRAND", "severity": SEV_WARN,
            "message": f"Champ 'brand' absent pour '{device_id}'",
            "suggested_action": "MANUAL_REVIEW",
        })

    # R6 — STORAGE CONFONDU AVEC RAM (12 ou 16 Go de stockage = impossible)
    if storage in (12, 16) and ram is None:
        anomalies.append({
            "rule": "STORAGE_AS_RAM", "severity": SEV_WARN,
            "message": f"storage={storage}Go suspect — probable confusion storage/RAM",
            "suggested_action": f"MOVE_STORAGE_{storage}_TO_RAM",
        })

    # R7 — MODÈLE RÉCENT SANS PRIX
    total_offers = sum(
        market_sales.get(c, {}).get("summary", {}).get("total_offers", 0)
        for c in ("new", "refurbished", "used")
    )
    if total_offers == 0 and RECENT_MODEL_RE.search(model):
        anomalies.append({
            "rule": "NO_PRICE_RECENT", "severity": SEV_INFO,
            "message": f"Modèle récent '{model}' sans aucune offre de vente",
            "suggested_action": "RESCRAPE",
        })

    # R8 — VARIANTES IPHONE SE (information pour déduplication future)
    if IPHONE_SE_VARIANT_RE.search(device_id):
        anomalies.append({
            "rule": "IPHONE_SE_VARIANT", "severity": SEV_INFO,
            "message": f"Variant iPhone SE : '{device_id}' — vérifier les doublons",
            "suggested_action": "MANUAL_REVIEW_DEDUP",
        })

    return anomalies


# ─────────────────────────────────────────────────────────────────────
# 3. RÉPARATIONS AUTOMATIQUES
# ─────────────────────────────────────────────────────────────────────
def auto_repair(
    device_id: str,
    doc: Dict[str, Any],
    anomalies: List[Dict[str, Any]],
    devices_dir: Path,
    db: Dict[str, Any],
) -> Tuple[bool, List[str]]:
    applied = []
    deleted = False

    for anomaly in anomalies:
        action = anomaly.get("suggested_action", "")
        rule = anomaly["rule"]

        if action == "DELETE" and not deleted:
            f = devices_dir / f"{device_id}.json"
            if f.exists():
                f.unlink()
            db.pop(device_id, None)
            applied.append(f"DELETED '{device_id}' (règle {rule})")
            deleted = True

        elif action.startswith("MOVE_STORAGE_") and not deleted:
            s = doc.get("storage")
            if s in (12, 16):
                doc["ram_gb"] = s
                doc["storage"] = None
                f = devices_dir / f"{device_id}.json"
                with open(f, "w", encoding="utf-8") as fh:
                    json.dump(doc, fh, indent=2, ensure_ascii=False)
                db[device_id] = doc
                applied.append(f"FIXED storage={s}→ram_gb={s} pour '{device_id}'")

        elif action == "REMOVE_LOW_OFFERS" and not deleted:
            modified = False
            for cond in ("new", "refurbished", "used"):
                cond_data = doc.get("market_sales", {}).get(cond, {})
                offers = cond_data.get("all_offers", [])
                clean = [o for o in offers if (o.get("price") or 0) >= SALE_PRICE_FLOOR]
                if len(clean) < len(offers):
                    cond_data["all_offers"] = clean
                    cond_data["summary"]["total_offers"] = len(clean)
                    cond_data["summary"]["min_price"] = min((o.get("price", 0) for o in clean), default=None)
                    modified = True
            if modified:
                f = devices_dir / f"{device_id}.json"
                with open(f, "w", encoding="utf-8") as fh:
                    json.dump(doc, fh, indent=2, ensure_ascii=False)
                db[device_id] = doc
                applied.append(f"REMOVED low-price offers pour '{device_id}'")

    return len(applied) > 0, applied


# ─────────────────────────────────────────────────────────────────────
# 4. AGENT PRINCIPAL
# ─────────────────────────────────────────────────────────────────────
def run_qa_agent(auto_repair_mode: bool = False, dataset_dir: Optional[Path] = None) -> Dict[str, Any]:
    devices_dir = dataset_dir or DEVICES_DIR
    unified_db_file = devices_dir.parent / "unified_catalog_database.json"

    print("=" * 80)
    print("🤖 [E-commerce AI Scraping QA Agent] Vérification du dataset unifié")
    print(f"📁 {devices_dir}")
    print(f"🔧 Auto-réparation : {'OUI' if auto_repair_mode else 'NON'}")
    print("=" * 80)

    if not devices_dir.exists():
        print(f"❌ Dossier introuvable : {devices_dir}")
        sys.exit(1)

    device_files = sorted(devices_dir.glob("*.json"))
    total = len(device_files)
    print(f"\n📊 {total} devices à analyser...\n")

    db: Dict[str, Any] = {}
    if unified_db_file.exists():
        with open(unified_db_file, "r", encoding="utf-8") as f:
            db = json.load(f)

    report: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_devices_analyzed": total,
        "auto_repair_enabled": auto_repair_mode,
        "summary": {
            SEV_CRIT: 0, SEV_WARN: 0, SEV_INFO: 0,
            "total_anomalies": 0, "devices_with_anomalies": 0,
            "devices_deleted": 0, "devices_repaired": 0,
        },
        "anomalies_by_rule": {},
        "devices_with_anomalies": [],
        "auto_repairs_applied": [],
    }

    for device_file in device_files:
        device_id = device_file.stem
        try:
            with open(device_file, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except Exception as e:
            report["devices_with_anomalies"].append({
                "device_id": device_id,
                "anomalies": [{"rule": "JSON_ERROR", "severity": SEV_CRIT, "message": str(e)}],
            })
            continue

        anomalies = detect_anomalies(device_id, doc)
        if not anomalies:
            continue

        report["summary"]["devices_with_anomalies"] += 1
        report["summary"]["total_anomalies"] += len(anomalies)
        for a in anomalies:
            sev = a["severity"]
            report["summary"][sev] = report["summary"].get(sev, 0) + 1
            rule = a["rule"]
            if rule not in report["anomalies_by_rule"]:
                report["anomalies_by_rule"][rule] = {"count": 0, "examples": []}
            report["anomalies_by_rule"][rule]["count"] += 1
            if len(report["anomalies_by_rule"][rule]["examples"]) < 5:
                report["anomalies_by_rule"][rule]["examples"].append(device_id)

        report["devices_with_anomalies"].append({"device_id": device_id, "anomalies": anomalies})

        if auto_repair_mode:
            repaired, actions = auto_repair(device_id, doc, anomalies, devices_dir, db)
            if repaired:
                report["auto_repairs_applied"].extend(actions)
                for a in actions:
                    print(f"  🔧 {a}")
                if any(an.get("suggested_action") == "DELETE" for an in anomalies):
                    report["summary"]["devices_deleted"] += 1
                else:
                    report["summary"]["devices_repaired"] += 1

    # Affichage résumé
    print("\n" + "=" * 80)
    print("📊 RAPPORT QA — RÉSUMÉ")
    print("=" * 80)
    print(f"  Devices analysés          : {total}")
    print(f"  Devices avec anomalies    : {report['summary']['devices_with_anomalies']}")
    print(f"  🔴 CRITIQUE               : {report['summary'].get(SEV_CRIT, 0)}")
    print(f"  🟠 WARNING                : {report['summary'].get(SEV_WARN, 0)}")
    print(f"  🟡 INFO                   : {report['summary'].get(SEV_INFO, 0)}")
    print(f"  Total anomalies           : {report['summary']['total_anomalies']}")
    if auto_repair_mode:
        print(f"  Devices supprimés         : {report['summary']['devices_deleted']}")
        print(f"  Devices corrigés          : {report['summary']['devices_repaired']}")

    print("\n📋 ANOMALIES PAR RÈGLE :")
    for rule, stats in sorted(report["anomalies_by_rule"].items(), key=lambda x: -x[1]["count"]):
        exs = ", ".join(stats["examples"][:3])
        print(f"  [{rule}] → {stats['count']} occurrence(s)  ex: {exs}")

    # Réécriture DB si réparations
    if auto_repair_mode and report["summary"]["devices_deleted"] > 0:
        print(f"\n💾 Réécriture base unifiée ({len(db)} devices)...")
        with open(unified_db_file, "w", encoding="utf-8") as f:
            json.dump(db, f, indent=2, ensure_ascii=False)
        print(f"  ✅ {unified_db_file}")

    # Sauvegarde rapport
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_file = QA_REPORTS_DIR / f"qa_report_{ts}.json"
    with open(report_file, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"\n📁 Rapport JSON : {report_file}")
    print("=" * 80)
    return report


# ─────────────────────────────────────────────────────────────────────
# 5. POINT D'ENTRÉE
# ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Agent QA automatique du merge E-commerce AI Scraping")
    parser.add_argument("--auto-repair", action="store_true", default=False)
    parser.add_argument("--dataset-dir", type=Path, default=None)
    args = parser.parse_args()

    report = run_qa_agent(auto_repair_mode=args.auto_repair, dataset_dir=args.dataset_dir)
    sys.exit(0 if report["summary"].get(SEV_CRIT, 0) == 0 else 1)
