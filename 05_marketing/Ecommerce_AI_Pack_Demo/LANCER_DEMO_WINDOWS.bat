@echo off
title Demo Commerciale E-commerce AI Scraping (Beta)
cd /d "%~dp0"

echo ==========================================================
echo     DEMARRAGE DE LA DEMO COMMERCIALE E-COMMERCE AI (BETA)
echo ==========================================================
echo Lancement du serveur local...

set PORT=5173

where python >nul 2>nul
if %ERRORLEVEL% equ 0 (
    start "" "http://localhost:%PORT%"
    python -m http.server %PORT% --directory application_web
    goto end
)

where python3 >nul 2>nul
if %ERRORLEVEL% equ 0 (
    start "" "http://localhost:%PORT%"
    python3 -m http.server %PORT% --directory application_web
    goto end
)

where npx >nul 2>nul
if %ERRORLEVEL% equ 0 (
    start "" "http://localhost:%PORT%"
    npx serve -s application_web -l %PORT%
    goto end
)

echo Utilisation du serveur PowerShell natif de Windows...
start "" "http://localhost:%PORT%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$port=%PORT%; $path=(Resolve-Path 'application_web').Path; $listener = New-Object System.Net.HttpListener; $listener.Prefixes.Add('http://localhost:' + $port + '/'); $listener.Start(); Write-Host 'Serveur actif sur http://localhost:'$port; while($listener.IsListening){ $context = $listener.GetContext(); $req = $context.Request; $res = $context.Response; $rel = $req.Url.LocalPath.TrimStart('/'); if($rel -eq ''){ $rel = 'index.html' }; $local = Join-Path $path $rel; if(Test-Path $local -PathType Leaf){ $bytes = [System.IO.File]::ReadAllBytes($local); $ext = [System.IO.Path]::GetExtension($local).ToLower(); switch($ext){ '.html' {$res.ContentType = 'text/html; charset=utf-8'} '.js' {$res.ContentType = 'application/javascript'} '.css' {$res.ContentType = 'text/css'} '.json' {$res.ContentType = 'application/json'} '.svg' {$res.ContentType = 'image/svg+xml'} default {$res.ContentType = 'application/octet-stream'} }; $res.ContentLength64 = $bytes.Length; $res.OutputStream.Write($bytes, 0, $bytes.Length) } else { $res.StatusCode = 404 }; $res.OutputStream.Close() }"

:end
pause
