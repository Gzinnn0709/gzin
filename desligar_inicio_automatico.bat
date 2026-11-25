@echo off
rem Desfaz o ligar_inicio_automatico.bat (o GabeCuts para de ligar sozinho com o Windows).
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\GabeCuts.bat" >nul 2>&1
echo Pronto: o GabeCuts nao liga mais sozinho com o Windows.
echo (Para ligar na mao: iniciar_cortes.bat. Para parar agora: parar_cortes.bat)
pause
