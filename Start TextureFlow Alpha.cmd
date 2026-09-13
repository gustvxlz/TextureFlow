@echo off
setlocal
cd /d "%~dp0"
if not exist "runtime\python\pythonw.exe" (
  echo Runtime portatil nao encontrado.
  echo Extraia novamente o pacote completo do TextureFlow Alpha.
  pause
  exit /b 1
)
set "PYTHONPATH=%~dp0src"
start "TextureFlow Alpha" "runtime\python\pythonw.exe" -m textureflow.app
endlocal

