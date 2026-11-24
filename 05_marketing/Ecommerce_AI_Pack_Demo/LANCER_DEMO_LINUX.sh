#!/bin/bash
cd "$(dirname "$0")" || exit 1
PORT=5173
(sleep 1 && (xdg-open "http://localhost:$PORT" || firefox "http://localhost:$PORT")) &
python3 -m http.server $PORT --directory application_web
