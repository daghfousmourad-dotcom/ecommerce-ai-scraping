#!/bin/bash
# Script de lancement rapide de la démo commerciale E-commerce AI Scraping
echo "=========================================================="
echo "    DÉMARRAGE DE LA DÉMO COMMERCIALE E-COMMERCE AI RÉPARATION    "
echo "=========================================================="
cd "$(dirname "$0")/ecommerce_sales_demo" || exit 1

if [ ! -d "node_modules" ]; then
  echo "Installation des dépendances..."
  npm install
fi

echo "Ouverture du serveur local..."
echo "Accédez à l'interface sur : http://localhost:5173"
npm run dev -- --open
