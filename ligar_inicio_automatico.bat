@echo off
rem Faz o GabeCuts ligar sozinho toda vez que o Windows iniciar (e se atualizar antes).
rem So precisa rodar UMA vez. Para desfazer: desligar_inicio_automatico.bat
cd /d "%~dp0"
if not exist ".venv\Scripts\gabecuts.exe" goto :sem_instalacao
> "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\GabeCuts.bat" echo @start "" /min cmd /c ""%~dp0auto_iniciar.bat""
echo.
echo  Pronto! O GabeCuts vai ligar sozinho sempre que o Windows iniciar.
echo  Quando a live comecar, ele abre a live no navegador e corta sozinho.
echo.
echo  Ligando agora tambem...
call "%~dp0iniciar_cortes.bat" auto
echo  Para desfazer: desligar_inicio_automatico.bat
echo.
pause
exit /b 0

:sem_instalacao
echo [ERRO] Instale o GabeCuts nesta pasta antes.
pause
exit /b 1
