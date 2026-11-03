@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

echo ==== Product card generator: setup and run ====
echo.

rem ---------- 1. Find Python ----------
set "PY="
py -3 --version >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if defined PY goto :have_python

python --version >nul 2>nul
if not errorlevel 1 set "PY=python"
if defined PY goto :have_python

if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set PY="%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if defined PY goto :have_python

rem ---------- 2. Install Python ----------
echo Python not found. Installing Python 3.12 for the current user...
where winget >nul 2>nul
if errorlevel 1 goto :download_python
winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set PY="%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if defined PY goto :have_python

:download_python
echo Trying the installer from python.org...
powershell -NoProfile -Command "Invoke-WebRequest -Uri https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe -OutFile '%TEMP%\python-installer.exe'"
if not exist "%TEMP%\python-installer.exe" goto :python_failed
"%TEMP%\python-installer.exe" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set PY="%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if defined PY goto :have_python

:python_failed
echo.
echo Could not install Python automatically.
echo Install it manually from https://www.python.org/downloads/
echo Tick the box "Add python.exe to PATH" in the installer, then run this file again.
pause
exit /b 1

:have_python
echo Using Python: %PY%
echo (No extra packages are needed - the service uses only the Python standard library.)

rem ---------- 3. API key ----------
if exist ".env" goto :have_env
echo.
echo A free API key is required. No bank card is needed for either option.
echo   1 = Groq        https://console.groq.com/keys
echo   2 = OpenRouter  https://openrouter.ai/keys
choice /c 12 /n /m "Which provider do you have a key for? Press 1 or 2: "
if errorlevel 2 goto :ask_openrouter

:ask_groq
set "APIKEY="
set /p APIKEY=Paste your Groq key and press Enter:
if "%APIKEY%"=="" goto :no_key
>.env echo GROQ_API_KEY=%APIKEY%
goto :key_saved

:ask_openrouter
set "APIKEY="
set /p APIKEY=Paste your OpenRouter key and press Enter:
if "%APIKEY%"=="" goto :no_key
>.env echo OPENROUTER_API_KEY=%APIKEY%

:key_saved
echo Key saved to .env - this file is not committed to git.

:have_env

rem ---------- 4. Run ----------
echo.
echo ---- Running: two products from the feed ----
%PY% -m cardgen examples\products.json -o examples\output
echo.
echo ---- Running: example from the task text ----
%PY% -m cardgen examples\sample_from_task.json -o examples\output

echo.
echo Done. Results are in the folder examples\output
explorer examples\output
pause
exit /b 0

:no_key
echo No key entered. Run this file again when you have one.
pause
exit /b 1
