# Scraper : 123COMPARER

## 📋 Présentation
scraper_123comparer.py
=======================
Scraper haute performance, exhaustif et agrégateur comparatif multi-marchands pour 123comparer.fr.
Cible : Comparateur de prix français et indépendant pour Smartphones Neufs, Reconditionnés & Occasion.

Valeur Stratégique Majeure pour E-commerce AI Scraping :
  - Pour chaque modèle de smartphone, 123comparer agrège en temps réel l'ensemble des offres
    marchandes du web français : Amazon, Fnac, Darty, Cdiscount, Boulanger, Rue du Commerce,
    Pixmania, Conforama, Galaxus, Kaufland, PcComponentes, SFR, Orange, Carrefour, Inmac, etc.
  - Chaque fiche produit permet d'extraire la dispersion complète des prix, les frais de livraison,
    l'état (Neuf, Reconditionné, Occasion), le marchand vendeur et le lien sortant officiel.

Architecture Hybride & Performances Extrêmes :
  1. Bypass Cloudflare Turnstile : Playwright initialise une session réelle Google Chrome
     (profil persistant /tmp/chrome_123_profile) pour obtenir et maintenir le cookie cf_clearance.
  2. Multi-Threading HTTP Haute Vitesse : Une fois le token obtenu, une session requests.Session
     parcourt le catalogue et extrait les fiches produits en parallèle (10 workers, ~25 req/s).
  3. Exhaustivité Totale : Parcourt l'ensemble de la pagination active (pages 1 à 172+),
     couvrant 100% des smartphones répertoriés avec des offres marchandes.

Conformité E-commerce AI V2 :
  - 23 colonnes standardisées (PRODUCT_SALE_KEYS)
  - Validation et canonicalisation via device_validator (marque, modèle, filtre smartphone, plancher prix)
  - 5 artefacts standardisés générés dans ./outputs/ :
      1. db_123comparer.json
      2. db_123comparer.jsonl
      3. db_123comparer.csv
      4. db_123comparer_run.json
      5. db_123comparer_issues.jsonl
  - Synchronisation stricte vers staging uniquement (outputs/). AUCUN envoi vers dataset/ ni GitHub.

- **Nom de la source** : `123comparer`
- **Domaine de données** : `product_sales`
- **URL cible** : https://www.123comparer.fr
- **Technologie principale** : `Playwright Headless Browser (Rendu JS & Contenu dynamique)`

---

## 🚀 Exécution autonome (Individuelle)

Pour exécuter ce scraper de façon totalement autonome et indépendante :

```bash
cd 02_scrapers/sources/123comparer
python3 scraper.py
```

Ou depuis la racine du projet :

```bash
python3 02_scrapers/sources/123comparer/scraper.py
```

---

## 📦 Fichiers générés (5 Artefacts standardisés)

Tous les résultats sont automatiquement enregistrés dans `02_scrapers/outputs/` :
1. `db_123comparer.csv` : Fichier tabulaire complet
2. `db_123comparer.json` : Export JSON structuré
3. `db_123comparer.jsonl` : Export JSON Lines pour ingestion streaming
4. `db_123comparer_run.json` : Métadonnées d'exécution (horodatage, durée, statut, colonnes)
5. `db_123comparer_issues.jsonl` : Journal d'audit et erreurs de parsing / anomalies

---

## 🔒 Configuration & Identifiants

Si des clés d'accès, proxies ou identifiants spécifiques sont nécessaires pour cette source, définissez-les dans le fichier central `.env` à la racine :

```env
123COMPARER_API_KEY=
123COMPARER_PROXY=
```
