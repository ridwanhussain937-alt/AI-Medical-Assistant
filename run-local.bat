@echo off
setlocal

if not exist ".venv\Scripts\python.exe" (
  echo Missing .venv. Run setup-local.bat first.
  exit /b 1
)

if not exist ".env" (
  echo Missing .env. Run setup-local.bat first.
  exit /b 1
)

call ".venv\Scripts\activate.bat"
if errorlevel 1 exit /b 1

python manage.py runserver 127.0.0.1:8000
