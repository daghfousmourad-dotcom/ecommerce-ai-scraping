# Scraper : SAVE

## 📋 Présentation
Save.co repair scraper: brand/model discovery via public HTML pages.

- **Nom de la source** : `save`
- **Domaine de données** : `repair_and_parts`
- **URL cible** : https://save.co
- **Technologie principale** : `API Directe / JSON State (Haute performance)`

---

## 🚀 Exécution autonome (Individuelle)

Pour exécuter ce scraper de façon totalement autonome et indépendante :

```bash
cd 02_scrapers/sources/save
python3 scraper.py
```

Ou depuis la racine du projet :

```bash
python3 02_scrapers/sources/save/scraper.py
```

---

## 📦 Fichiers générés (5 Artefacts standardisés)

Tous les résultats sont automatiquement enregistrés dans `02_scrapers/outputs/` :
1. `db_save.csv` : Fichier tabulaire complet
2. `db_save.json` : Export JSON structuré
3. `db_save.jsonl` : Export JSON Lines pour ingestion streaming
4. `db_save_run.json` : Métadonnées d'exécution (horodatage, durée, statut, colonnes)
5. `db_save_issues.jsonl` : Journal d'audit et erreurs de parsing / anomalies

---

## 🔒 Configuration & Identifiants

Si des clés d'accès, proxies ou identifiants spécifiques sont nécessaires pour cette source, définissez-les dans le fichier central `.env` à la racine :

```env
SAVE_API_KEY=
SAVE_PROXY=
```
