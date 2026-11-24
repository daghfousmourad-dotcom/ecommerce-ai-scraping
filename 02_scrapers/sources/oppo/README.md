# Scraper : OPPO

## 📋 Présentation
OPPO France Scraper (https://support.oppo.com/fr/spare-parts-price/).

Tarifs officiels des pièces détachées et réparations constructeur OPPO France :
Stratégie ordonnée (Roadmap E-commerce AI Scraping) :
  1. API Directe (POST JSON REST) :
     - Liste des modèles officiels : POST https://par-sow-cms.oppo.com/oppo-api/basic/v1/getProduct
     - Tarifs indicatifs des pièces par modèle : POST https://par-sow-cms.oppo.com/oppo-api/basic/v1/getPartPriceNew
     Fournit 100% des tarifs officiels constructeur OPPO en France en quelques secondes.

- **Nom de la source** : `oppo`
- **Domaine de données** : `repair_and_parts`
- **URL cible** : https://support.oppo.com/fr/spare-parts-price/).
- **Technologie principale** : `API Directe / JSON State (Haute performance)`

---

## 🚀 Exécution autonome (Individuelle)

Pour exécuter ce scraper de façon totalement autonome et indépendante :

```bash
cd 02_scrapers/sources/oppo
python3 scraper.py
```

Ou depuis la racine du projet :

```bash
python3 02_scrapers/sources/oppo/scraper.py
```

---

## 📦 Fichiers générés (5 Artefacts standardisés)

Tous les résultats sont automatiquement enregistrés dans `02_scrapers/outputs/` :
1. `db_oppo.csv` : Fichier tabulaire complet
2. `db_oppo.json` : Export JSON structuré
3. `db_oppo.jsonl` : Export JSON Lines pour ingestion streaming
4. `db_oppo_run.json` : Métadonnées d'exécution (horodatage, durée, statut, colonnes)
5. `db_oppo_issues.jsonl` : Journal d'audit et erreurs de parsing / anomalies

---

## 🔒 Configuration & Identifiants

Si des clés d'accès, proxies ou identifiants spécifiques sont nécessaires pour cette source, définissez-les dans le fichier central `.env` à la racine :

```env
OPPO_API_KEY=
OPPO_PROXY=
```
