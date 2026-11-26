@echo off
rem Liga o GabeCuts minimizado e com prioridade baixa (a live no navegador vem primeiro).
rem Quando a live comeca, o proprio GabeCuts abre a live no navegador (pontos) e corta sozinho.
rem "iniciar_cortes.bat auto" = modo silencioso, usado pelo inicio automatico do Windows.
cd /d "%~dp0"

rem ===== Configuracao =====
rem Plataforma da live: kick ou twitch
set PLATAFORMA=kick
set CANAL=gabepeixe
rem ========================

if not exist ".venv\Scripts\gabecuts.exe" goto :sem_instalacao

rem ja esta rodando? (o vigia ou o proprio programa)
tasklist /V /FI "IMAGENAME eq cmd.exe" /FI "WINDOWTITLE eq GabeCuts*" | find /I "cmd.exe" >nul
if not errorlevel 1 goto :ja_rodando
tasklist /FI "IMAGENAME eq gabecuts.exe" | find /I "gabecuts.exe" >nul
if not errorlevel 1 goto :ja_rodando

rem o vigia liga o GabeCuts e religa sozinho se ele fechar
start "GabeCuts" /min /belownormal cmd /c ""%~dp0vigia.bat" %PLATAFORMA% %CANAL%"
if /I "%~1"=="auto" exit /b 0
echo.
echo  GabeCuts ligado em segundo plano - janela minimizada na barra de tarefas.
echo  Quando a live comecar, ele abre a live no navegador e corta sozinho.
echo.
echo  Cortes no site:    https://www.aamcell.com.br/cortes
echo  Painel local:      http://localhost:8000
echo  Para parar:        parar_cortes.bat
echo.
timeout /t 10
exit /b 0

:ja_rodando
if /I "%~1"=="auto" exit /b 0
echo O GabeCuts ja esta rodando. Painel: http://localhost:8000
timeout /t 6
exit /b 0

:sem_instalacao
if /I "%~1"=="auto" exit /b 1
echo [ERRO] Instalacao nao encontrada nesta pasta. Rode antes, no Prompt:
echo    py -3.13 -m venv .venv
echo    .venv\Scripts\activate
echo    pip install -e ".[whisper,kick]"
pause
exit /b 1
