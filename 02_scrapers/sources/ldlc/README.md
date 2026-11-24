# Scraper : LDLC

## 📋 Présentation
scraper_ldlc.py
===============
Scraper haute performance pour LDLC (Smartphones Neufs & Reconditionnés).
Cible : Enseigne N°23 (Priorité 1) du catalogue E-commerce AI Scraping, présent à Marseille Grand V & Plan de Campagne.

Univers : product_sales (23 colonnes standardisées E-commerce AI V2)
Périmètre d'extraction :
  - Smartphones Neufs : https://www.ldlc.com/telephonie/telephonie-portable/mobile-smartphone/c4416/
  - Smartphones Reconditionnés : https://www.ldlc.com/telephonie/telephonie-portable/smartphone-reconditionne/c7931/

Génère 5 fichiers d'artefacts dans ./outputs/ :
  1. db_ldlc.json
  2. db_ldlc.jsonl
  3. db_ldlc.csv
  4. db_ldlc_run.json
  5. db_ldlc_issues.jsonl

- **Nom de la source** : `ldlc`
- **Domaine de données** : `product_sales`
- **URL cible** : https://www.ldlc.com/telephonie/telephonie-portable/mobile-smartphone/c4416/
- **Technologie principale** : `API Directe / JSON State (Haute performance)`

---

## 🚀 Exécution autonome (Individuelle)

Pour exécuter ce scraper de façon totalement autonome et indépendante :

```bash
cd 02_scrapers/sources/ldlc
python3 scraper.py
```

Ou depuis la racine du projet :

```bash
python3 02_scrapers/sources/ldlc/scraper.py
```

---

## 📦 Fichiers générés (5 Artefacts standardisés)

Tous les résultats sont automatiquement enregistrés dans `02_scrapers/outputs/` :
1. `db_ldlc.csv` : Fichier tabulaire complet
2. `db_ldlc.json` : Export JSON structuré
3. `db_ldlc.jsonl` : Export JSON Lines pour ingestion streaming
4. `db_ldlc_run.json` : Métadonnées d'exécution (horodatage, durée, statut, colonnes)
5. `db_ldlc_issues.jsonl` : Journal d'audit et erreurs de parsing / anomalies

---

## 🔒 Configuration & Identifiants

Si des clés d'accès, proxies ou identifiants spécifiques sont nécessaires pour cette source, définissez-les dans le fichier central `.env` à la racine :

```env
LDLC_API_KEY=
LDLC_PROXY=
```
