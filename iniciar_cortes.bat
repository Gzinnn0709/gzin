@echo off
rem Inicia o GabeCuts minimizado e com prioridade baixa (a live no navegador vem primeiro).
cd /d "%~dp0"
if not exist ".venv\Scripts\gabecuts.exe" goto :sem_instalacao

rem Plataforma da live: kick ou twitch
set PLATAFORMA=kick

tasklist /FI "IMAGENAME eq gabecuts.exe" | find /I "gabecuts.exe" >nul
if not errorlevel 1 goto :ja_rodando

start "GabeCuts" /min /belownormal ".venv\Scripts\gabecuts.exe" --platform %PLATAFORMA% live
echo.
echo  GabeCuts rodando em segundo plano - janela minimizada na barra de tarefas.
echo  Pode assistir a live normalmente.
echo.
echo  Painel dos cortes: http://localhost:8000
echo  Cortes salvos em:  %~dp0data\clips
echo  Para parar:        clique duas vezes em parar_cortes.bat
echo.
timeout /t 10
exit /b 0

:ja_rodando
echo O GabeCuts ja esta rodando. Painel: http://localhost:8000
timeout /t 6
exit /b 0

:sem_instalacao
echo [ERRO] Instalacao nao encontrada nesta pasta. Rode antes, no Prompt:
echo    py -3.13 -m venv .venv
echo    .venv\Scripts\activate
echo    pip install -e ".[whisper,kick]"
pause
exit /b 1
