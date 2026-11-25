@echo off
rem Um clique: abre a live no navegador (pontos) e liga os cortes automaticos em segundo plano.
cd /d "%~dp0"

rem ===== Configuracao =====
rem Plataforma da live: kick ou twitch
set PLATAFORMA=kick
set CANAL=gabepeixe
rem Abrir a live no navegador junto (para ganhar os pontos): sim ou nao
set ABRIR_LIVE=sim
rem ========================

if not exist ".venv\Scripts\gabecuts.exe" goto :sem_instalacao

tasklist /FI "IMAGENAME eq gabecuts.exe" | find /I "gabecuts.exe" >nul
if not errorlevel 1 goto :ja_rodando

start "GabeCuts" /min /belownormal ".venv\Scripts\gabecuts.exe" --platform %PLATAFORMA% --channel %CANAL% live
call :abrir_live
echo.
echo  GabeCuts rodando em segundo plano - janela minimizada na barra de tarefas.
echo  A live foi aberta no navegador: deixe la para ganhar os pontos.
echo  Dica: no player da live, use 480p ou 360p para deixar o PC mais leve.
echo.
echo  Painel dos cortes: http://localhost:8000
echo  Cortes salvos em:  %~dp0data\clips
echo  Para parar:        clique duas vezes em parar_cortes.bat
echo.
timeout /t 10
exit /b 0

:ja_rodando
call :abrir_live
echo O GabeCuts ja esta rodando. Painel: http://localhost:8000
timeout /t 6
exit /b 0

:abrir_live
if /I not "%ABRIR_LIVE%"=="sim" exit /b 0
if /I "%PLATAFORMA%"=="twitch" (start "" "https://www.twitch.tv/%CANAL%") else (start "" "https://kick.com/%CANAL%")
exit /b 0

:sem_instalacao
echo [ERRO] Instalacao nao encontrada nesta pasta. Rode antes, no Prompt:
echo    py -3.13 -m venv .venv
echo    .venv\Scripts\activate
echo    pip install -e ".[whisper,kick]"
pause
exit /b 1
