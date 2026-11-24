# Scraper : EPHONEACCESS

## 📋 Présentation
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

- **Nom de la source** : `ephoneaccess`
- **Domaine de données** : `product_sales`
- **URL cible** : https://www.ephoneaccess.com
- **Technologie principale** : `API Directe / JSON State (Haute performance)`

---

## 🚀 Exécution autonome (Individuelle)

Pour exécuter ce scraper de façon totalement autonome et indépendante :

```bash
cd 02_scrapers/sources/ephoneaccess
python3 scraper.py
```

Ou depuis la racine du projet :

```bash
python3 02_scrapers/sources/ephoneaccess/scraper.py
```

---

## 📦 Fichiers générés (5 Artefacts standardisés)

Tous les résultats sont automatiquement enregistrés dans `02_scrapers/outputs/` :
1. `db_ephoneaccess.csv` : Fichier tabulaire complet
2. `db_ephoneaccess.json` : Export JSON structuré
3. `db_ephoneaccess.jsonl` : Export JSON Lines pour ingestion streaming
4. `db_ephoneaccess_run.json` : Métadonnées d'exécution (horodatage, durée, statut, colonnes)
5. `db_ephoneaccess_issues.jsonl` : Journal d'audit et erreurs de parsing / anomalies

---

## 🔒 Configuration & Identifiants

Si des clés d'accès, proxies ou identifiants spécifiques sont nécessaires pour cette source, définissez-les dans le fichier central `.env` à la racine :

```env
EPHONEACCESS_API_KEY=
EPHONEACCESS_PROXY=
```
