@echo off
rem Para o GabeCuts e os ffmpeg/streamlink que ele abriu.
rem primeiro o vigia (senao ele religa), depois o programa
taskkill /FI "IMAGENAME eq cmd.exe" /FI "WINDOWTITLE eq GabeCuts*" /T /F >nul 2>&1
taskkill /IM gabecuts.exe /T /F >nul 2>&1
tasklist /FI "IMAGENAME eq gabecuts.exe" | find /I "gabecuts.exe" >nul
if not errorlevel 1 (echo Nao consegui parar: feche a janela "GabeCuts" na barra de tarefas.) else (echo GabeCuts parado. Os cortes ja feitos continuam em data\clips.)
timeout /t 5
