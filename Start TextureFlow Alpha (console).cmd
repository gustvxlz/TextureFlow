@echo off
setlocal
cd /d "%~dp0"
if not exist "runtime\python\python.exe" (
  echo Runtime portatil nao encontrado.
  pause
  exit /b 1
)
set "PYTHONPATH=%~dp0src"
"runtime\python\python.exe" -m textureflow.app
if errorlevel 1 pause
endlocal
