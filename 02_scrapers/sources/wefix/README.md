# Scraper : WEFIX

## 📋 Présentation
WeFix scraper: discover model URLs via HTTP, scrape prices with HTTP.

Le site WeFix a été refondu et utilise maintenant de simples liens HTML
statiques pour la navigation device/marque/modèle — plus besoin de Playwright.

Flux de découverte :
  1. GET /reparation/smartphone  → liens vers chaque marque
  2. GET /reparation/smartphone/{brand}?tag=1  → liens vers chaque modèle
  3. Scraper chaque page modèle pour en extraire les prix de réparation

- **Nom de la source** : `wefix`
- **Domaine de données** : `repair_and_parts`
- **URL cible** : https://www.wefix.net
- **Technologie principale** : `Playwright Headless Browser (Rendu JS & Contenu dynamique)`

---

## 🚀 Exécution autonome (Individuelle)

Pour exécuter ce scraper de façon totalement autonome et indépendante :

```bash
cd 02_scrapers/sources/wefix
python3 scraper.py
```

Ou depuis la racine du projet :

```bash
python3 02_scrapers/sources/wefix/scraper.py
```

---

## 📦 Fichiers générés (5 Artefacts standardisés)

Tous les résultats sont automatiquement enregistrés dans `02_scrapers/outputs/` :
1. `db_wefix.csv` : Fichier tabulaire complet
2. `db_wefix.json` : Export JSON structuré
3. `db_wefix.jsonl` : Export JSON Lines pour ingestion streaming
4. `db_wefix_run.json` : Métadonnées d'exécution (horodatage, durée, statut, colonnes)
5. `db_wefix_issues.jsonl` : Journal d'audit et erreurs de parsing / anomalies

---

## 🔒 Configuration & Identifiants

Si des clés d'accès, proxies ou identifiants spécifiques sont nécessaires pour cette source, définissez-les dans le fichier central `.env` à la racine :

```env
WEFIX_API_KEY=
WEFIX_PROXY=
```
