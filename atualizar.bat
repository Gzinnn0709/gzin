@echo off
rem Baixa a versao mais nova do GabeCuts, atualiza as dependencias e liga de novo.
rem Tudo dentro de um bloco ( ): o Windows le o bloco inteiro antes de rodar, entao este
rem proprio arquivo pode ser atualizado sem atrapalhar.
setlocal EnableDelayedExpansion
(
  cd /d "%~dp0"
  echo Parando o GabeCuts antigo...
  taskkill /FI "IMAGENAME eq cmd.exe" /FI "WINDOWTITLE eq GabeCuts*" /T /F >nul 2>&1
  taskkill /IM gabecuts.exe /T /F >nul 2>&1
  echo Baixando a versao nova...
  for /f %%b in ('git rev-parse --abbrev-ref HEAD') do set "RAMO=%%b"
  git fetch -q origin !RAMO!
  if errorlevel 1 (
    echo.
    echo [ERRO] Nao consegui baixar a versao nova. Confira a internet e tire uma foto desta tela.
    pause
    exit /b 1
  )
  rem descarta mudancas feitas a mao nos arquivos do projeto (seus cortes em data\ nao sao tocados)
  git reset -q --hard origin/!RAMO!
  echo Atualizando dependencias...
  ".venv\Scripts\python.exe" -m pip install -q -e ".[whisper,kick]"
  if errorlevel 1 (
    echo.
    echo [ERRO] Nao consegui instalar as dependencias. Tire uma foto desta tela.
    pause
    exit /b 1
  )
  echo.
  echo Atualizado! Ligando o GabeCuts de novo...
  call "%~dp0iniciar_cortes.bat" auto
  timeout /t 8 /nobreak >nul
  start "" "http://localhost:8000"
  echo Pronto. Agora, em aamcell.com.br/cortes, clique em "Conectar de novo".
  pause
  exit /b 0
)
