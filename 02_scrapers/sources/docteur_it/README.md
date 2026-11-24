# Scraper : DOCTEUR_IT

## 📋 Présentation
Docteur IT scraper: discover model URLs via HTTP, scrape prices with HTTP.

Flux de découverte :
  1. GET https://www.docteur-it.com/tarbes-meridien-sub/index/ (Magasin de référence)
  2. Parse de l'arborescence du méga-menu :
     - li.dropdown (Catégorie : Téléphone, Tablette, Console...)
     - li.dropdown-submenu (Marque : Apple, Samsung, Huawei...)
     - ul.dropdown-menu a[href*='-produit-'] (Modèle : iPhone 11, Galaxy S20...)
  3. Scraper chaque page modèle en parallèle (multithreadé) pour extraire les tarifs de réparation.

- **Nom de la source** : `docteur_it`
- **Domaine de données** : `repair_and_parts`
- **URL cible** : https://www.docteur-it.com/tarbes-meridien-sub/index/
- **Technologie principale** : `API Directe / JSON State (Haute performance)`

---

## 🚀 Exécution autonome (Individuelle)

Pour exécuter ce scraper de façon totalement autonome et indépendante :

```bash
cd 02_scrapers/sources/docteur_it
python3 scraper.py
```

Ou depuis la racine du projet :

```bash
python3 02_scrapers/sources/docteur_it/scraper.py
```

---

## 📦 Fichiers générés (5 Artefacts standardisés)

Tous les résultats sont automatiquement enregistrés dans `02_scrapers/outputs/` :
1. `db_docteur_it.csv` : Fichier tabulaire complet
2. `db_docteur_it.json` : Export JSON structuré
3. `db_docteur_it.jsonl` : Export JSON Lines pour ingestion streaming
4. `db_docteur_it_run.json` : Métadonnées d'exécution (horodatage, durée, statut, colonnes)
5. `db_docteur_it_issues.jsonl` : Journal d'audit et erreurs de parsing / anomalies

---

## 🔒 Configuration & Identifiants

Si des clés d'accès, proxies ou identifiants spécifiques sont nécessaires pour cette source, définissez-les dans le fichier central `.env` à la racine :

```env
DOCTEUR_IT_API_KEY=
DOCTEUR_IT_PROXY=
```
