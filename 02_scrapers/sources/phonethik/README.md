# Scraper : PHONETHIK

## 📋 Présentation
Phon'Ethik Lyon Scraper (https://www.phon-ethik.fr).

Atelier de réparation smartphone éco-responsable (Lyon Part-Dieu).
Stratégie ordonnée (Roadmap E-commerce AI Scraping) :
  1. API Directe / JSON Embedded State : Les tarifs complets sont intégrés dans la page
     sous la variable JavaScript ``window.ITELIER_PRICE_DATA`` (910+ produits, 730 modèles smartphones).
  2. Scraping HTML direct ultra-rapide (HTTP session + JSON parser) :
     Permet d'extraire l'intégralité des 6 700+ tarifs smartphone en moins de 2 secondes.

- **Nom de la source** : `phonethik`
- **Domaine de données** : `product_sales`
- **URL cible** : https://www.phon-ethik.fr).
- **Technologie principale** : `API Directe / JSON State (Haute performance)`

---

## 🚀 Exécution autonome (Individuelle)

Pour exécuter ce scraper de façon totalement autonome et indépendante :

```bash
cd 02_scrapers/sources/phonethik
python3 scraper.py
```

Ou depuis la racine du projet :

```bash
python3 02_scrapers/sources/phonethik/scraper.py
```

---

## 📦 Fichiers générés (5 Artefacts standardisés)

Tous les résultats sont automatiquement enregistrés dans `02_scrapers/outputs/` :
1. `db_phonethik.csv` : Fichier tabulaire complet
2. `db_phonethik.json` : Export JSON structuré
3. `db_phonethik.jsonl` : Export JSON Lines pour ingestion streaming
4. `db_phonethik_run.json` : Métadonnées d'exécution (horodatage, durée, statut, colonnes)
5. `db_phonethik_issues.jsonl` : Journal d'audit et erreurs de parsing / anomalies

---

## 🔒 Configuration & Identifiants

Si des clés d'accès, proxies ou identifiants spécifiques sont nécessaires pour cette source, définissez-les dans le fichier central `.env` à la racine :

```env
PHONETHIK_API_KEY=
PHONETHIK_PROXY=
```
