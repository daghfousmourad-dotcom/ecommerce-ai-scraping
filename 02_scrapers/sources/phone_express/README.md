# Scraper : PHONE_EXPRESS

## 📋 Présentation
Phone Express Scraper (https://phoneexpress.fr/tarifs).

Grille tarifaire structurée de réparation smartphones et tablettes à Martigues :
Stratégie ordonnée (Roadmap E-commerce AI Scraping) :
  2. Parsing HTML Direct (BeautifulSoup / Requests) :
     - Téléchargement direct de https://phoneexpress.fr/tarifs
     - Extraction des 4 tables HTML exhaustives : iPhone, Samsung Galaxy, Marques Android (Xiaomi, Huawei, OnePlus, Pixel, Oppo), et Tablettes (iPad, Galaxy Tab)
     - Expansion des modèles groupés (ex: 'iPhone 8 / 8 Plus' -> 'iPhone 8', 'iPhone 8 Plus')
     - Normalisation des pannes constructeurs (Écran, Batterie, Connecteur, Caméra Arrière, Vitre Arrière)

- **Nom de la source** : `phone_express`
- **Domaine de données** : `product_sales`
- **URL cible** : https://phoneexpress.fr/tarifs).
- **Technologie principale** : `API Directe / JSON State (Haute performance)`

---

## 🚀 Exécution autonome (Individuelle)

Pour exécuter ce scraper de façon totalement autonome et indépendante :

```bash
cd 02_scrapers/sources/phone_express
python3 scraper.py
```

Ou depuis la racine du projet :

```bash
python3 02_scrapers/sources/phone_express/scraper.py
```

---

## 📦 Fichiers générés (5 Artefacts standardisés)

Tous les résultats sont automatiquement enregistrés dans `02_scrapers/outputs/` :
1. `db_phone_express.csv` : Fichier tabulaire complet
2. `db_phone_express.json` : Export JSON structuré
3. `db_phone_express.jsonl` : Export JSON Lines pour ingestion streaming
4. `db_phone_express_run.json` : Métadonnées d'exécution (horodatage, durée, statut, colonnes)
5. `db_phone_express_issues.jsonl` : Journal d'audit et erreurs de parsing / anomalies

---

## 🔒 Configuration & Identifiants

Si des clés d'accès, proxies ou identifiants spécifiques sont nécessaires pour cette source, définissez-les dans le fichier central `.env` à la racine :

```env
PHONE_EXPRESS_API_KEY=
PHONE_EXPRESS_PROXY=
```
