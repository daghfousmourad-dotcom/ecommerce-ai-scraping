#!/bin/bash
cd "$(dirname "$0")" || exit 1

echo "=========================================================="
echo "    DÉMARRAGE DE LA DÉMO COMMERCIALE E-COMMERCE AI (BÊTA)        "
echo "=========================================================="
echo "Lancement du serveur local optimisé..."

PORT=5173
if lsof -Pi :$PORT -sTCP:LISTEN -t >/dev/null 2>&1; then
    PORT=5182
fi

(sleep 1 && open "http://localhost:$PORT") &

if command -v python3 >/dev/null 2>&1; then
    python3 -m http.server $PORT --directory application_web
elif command -v python >/dev/null 2>&1; then
    python -m http.server $PORT --directory application_web
elif command -v npx >/dev/null 2>&1; then
    npx serve -s application_web -l $PORT
else
    echo "Erreur : Aucun environnement d'exécution trouvé (python3 ou node requis)."
    read -p "Appuyez sur Entrée pour quitter..."
fi
