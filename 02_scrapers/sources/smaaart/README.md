# Scraper : SMAAART

## 📋 Présentation
scraper_smaaart.py
==================
Scraper haute performance pour SMAAART (Smartphones Reconditionnés en France).
Reconditionneur certifié français situé en région Occitanie / PACA.
Garantie 24 mois, tests sur 60 points de contrôle, livraison express 24-48h sur Marseille.

Univers : product_sales (23 colonnes standardisées E-commerce AI V2)
Périmètre d'extraction :
  - Catalogue complet Téléphonie / Smartphones Reconditionnés :
    https://smaaart.fr/12-smartphones-reconditionnes?page=N
  - Extraction multi-variantes par grade esthétique (État correct, Très bon état, Parfait état)
  - Exclusion stricte des ruptures de stock ("Bientôt disponible")

Génère 5 fichiers d'artefacts standardisés dans ./outputs/ :
  1. db_smaaart.json
  2. db_smaaart.jsonl
  3. db_smaaart.csv
  4. db_smaaart_run.json
  5. db_smaaart_issues.jsonl

Note : Conforme à la consigne de staging manuel (pas d'export automatique vers dataset/).

- **Nom de la source** : `smaaart`
- **Domaine de données** : `product_sales`
- **URL cible** : https://smaaart.fr/12-smartphones-reconditionnes?page=N
- **Technologie principale** : `API Directe / JSON State (Haute performance)`

---

## 🚀 Exécution autonome (Individuelle)

Pour exécuter ce scraper de façon totalement autonome et indépendante :

```bash
cd 02_scrapers/sources/smaaart
python3 scraper.py
```

Ou depuis la racine du projet :

```bash
python3 02_scrapers/sources/smaaart/scraper.py
```

---

## 📦 Fichiers générés (5 Artefacts standardisés)

Tous les résultats sont automatiquement enregistrés dans `02_scrapers/outputs/` :
1. `db_smaaart.csv` : Fichier tabulaire complet
2. `db_smaaart.json` : Export JSON structuré
3. `db_smaaart.jsonl` : Export JSON Lines pour ingestion streaming
4. `db_smaaart_run.json` : Métadonnées d'exécution (horodatage, durée, statut, colonnes)
5. `db_smaaart_issues.jsonl` : Journal d'audit et erreurs de parsing / anomalies

---

## 🔒 Configuration & Identifiants

Si des clés d'accès, proxies ou identifiants spécifiques sont nécessaires pour cette source, définissez-les dans le fichier central `.env` à la racine :

```env
SMAAART_API_KEY=
SMAAART_PROXY=
```
