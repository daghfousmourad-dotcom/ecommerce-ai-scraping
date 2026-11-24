# Scraper : PIXEL_STORE

## 📋 Présentation
Pixel Store France Scraper (https://pixelstorefrance.fr).

Simulateur de devis de réparation smartphone Pixel Store :
- URL de départ : https://pixelstorefrance.fr/devis
- Parcours humain Playwright :
    1. Sélectionner 'Smartphone' -> cliquer sur 'Suivant'
    2. Pour chaque marque affichée -> la sélectionner, cliquer sur 'Suivant'
    3. Sélectionner un modèle dans la liste -> cliquer sur 'Suivant'
    4. Cocher toutes les réparations affichées
    5. Cliquer sur 'Découvrir mon devis instantané' (#ds-reveal-btn)
    6. Sur la modale pop-up (#ds-popup), cliquer sur 'Non merci' (#ds-popup-no)
    7. Extraire la liste des réparations cochées avec le prix affiché (#ds-reveal-list)
- Extraction complète du catalogue (Apple + Android / 930+ modèles) via le contexte navigateur.

- **Nom de la source** : `pixel_store`
- **Domaine de données** : `product_sales`
- **URL cible** : https://pixelstorefrance.fr).
- **Technologie principale** : `Playwright Headless Browser (Rendu JS & Contenu dynamique)`

---

## 🚀 Exécution autonome (Individuelle)

Pour exécuter ce scraper de façon totalement autonome et indépendante :

```bash
cd 02_scrapers/sources/pixel_store
python3 scraper.py
```

Ou depuis la racine du projet :

```bash
python3 02_scrapers/sources/pixel_store/scraper.py
```

---

## 📦 Fichiers générés (5 Artefacts standardisés)

Tous les résultats sont automatiquement enregistrés dans `02_scrapers/outputs/` :
1. `db_pixel_store.csv` : Fichier tabulaire complet
2. `db_pixel_store.json` : Export JSON structuré
3. `db_pixel_store.jsonl` : Export JSON Lines pour ingestion streaming
4. `db_pixel_store_run.json` : Métadonnées d'exécution (horodatage, durée, statut, colonnes)
5. `db_pixel_store_issues.jsonl` : Journal d'audit et erreurs de parsing / anomalies

---

## 🔒 Configuration & Identifiants

Si des clés d'accès, proxies ou identifiants spécifiques sont nécessaires pour cette source, définissez-les dans le fichier central `.env` à la racine :

```env
PIXEL_STORE_API_KEY=
PIXEL_STORE_PROXY=
```
