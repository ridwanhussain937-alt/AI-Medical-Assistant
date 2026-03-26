@echo off
setlocal

if not exist ".venv\Scripts\python.exe" (
  echo Creating local virtual environment...
  python -m venv .venv
  if errorlevel 1 exit /b 1
)

call ".venv\Scripts\activate.bat"
if errorlevel 1 exit /b 1

echo Installing Python dependencies into .venv...
python -m pip install -r requirements.txt
if errorlevel 1 exit /b 1

if not exist ".env" (
  echo Creating local .env from .env.example...
  copy /Y ".env.example" ".env" >nul
  if errorlevel 1 exit /b 1
)

echo Applying database migrations...
python manage.py migrate --noinput
if errorlevel 1 exit /b 1

echo Synchronizing default records...
python manage.py bootstrap_defaults
if errorlevel 1 exit /b 1

echo.
echo Local setup complete.
echo Use run-local.bat to start the development server.
