@echo off
rem Para o GabeCuts e os ffmpeg/streamlink que ele abriu.
taskkill /IM gabecuts.exe /T /F >nul 2>&1
if errorlevel 1 (echo O GabeCuts nao estava rodando.) else (echo GabeCuts parado. Os cortes ja feitos continuam em data\clips.)
timeout /t 5
