@echo off
rem Mantem o GabeCuts ligado: se ele fechar por qualquer motivo, liga de novo em 20 s.
rem Aberto pelo iniciar_cortes.bat (janela minimizada "GabeCuts"). Para parar: parar_cortes.bat
title GabeCuts
cd /d "%~dp0"
if not exist data mkdir data
:loop
".venv\Scripts\gabecuts.exe" --platform %1 --channel %2 live
echo %date% %time% GabeCuts fechou (codigo %errorlevel%), ligando de novo em 20 s>> data\vigia.log
timeout /t 20 /nobreak >nul
goto loop
