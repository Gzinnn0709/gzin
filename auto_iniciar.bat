@echo off
rem Chamado pelo Windows ao iniciar (veja ligar_inicio_automatico.bat). Nao precisa abrir na mao.
rem Espera a internet, atualiza o GabeCuts e liga ele em segundo plano.
rem Tudo fica dentro de um bloco ( ) de proposito: o Windows le o bloco inteiro antes de rodar,
rem entao o "git pull" pode atualizar este proprio arquivo sem atrapalhar.
setlocal EnableDelayedExpansion
(
  cd /d "%~dp0"
  timeout /t 30 /nobreak >nul
  set "ANTES="
  set "DEPOIS="
  for /f %%i in ('git rev-parse HEAD:pyproject.toml 2^>nul') do set "ANTES=%%i"
  git pull -q --ff-only --autostash >nul 2>&1
  for /f %%i in ('git rev-parse HEAD:pyproject.toml 2^>nul') do set "DEPOIS=%%i"
  if not "!ANTES!"=="!DEPOIS!" ".venv\Scripts\python.exe" -m pip install -q -e ".[whisper,kick]" >nul 2>&1
  call "%~dp0iniciar_cortes.bat" auto
  exit /b 0
)
