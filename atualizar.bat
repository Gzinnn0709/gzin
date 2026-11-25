@echo off
rem Baixa a versao mais nova do GabeCuts e atualiza as dependencias.
cd /d "%~dp0"
taskkill /IM gabecuts.exe /T /F >nul 2>&1
git pull
if errorlevel 1 goto :erro
".venv\Scripts\python.exe" -m pip install -q -e ".[whisper,kick]"
if errorlevel 1 goto :erro
echo.
echo Atualizado! Clique duas vezes em iniciar_cortes.bat para rodar.
pause
exit /b 0
:erro
echo.
echo [ERRO] Nao consegui atualizar. Tire uma foto desta tela.
pause
exit /b 1
