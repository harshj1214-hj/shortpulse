@echo off
rem ShortPulse helper for Windows. Works from PowerShell or cmd:
rem   .\windows.cmd setup   create .venv, install packages, build the C engine
rem   .\windows.cmd build   compile engine\shortpulse.dll with gcc
rem   .\windows.cmd test    C tests + Python tests
rem   .\windows.cmd run     dev server on http://127.0.0.1:5000 with demo data
rem   .\windows.cmd train   retrain both ML models
setlocal
cd /d "%~dp0"
set "VPY=%~dp0.venv\Scripts\python.exe"

if /i "%~1"=="setup" goto setup
if /i "%~1"=="build" goto build
if /i "%~1"=="test" goto test
if /i "%~1"=="run" goto run
if /i "%~1"=="train" goto train
echo Usage: .\windows.cmd setup ^| build ^| test ^| run ^| train
exit /b 1

:setup
set "PY=python"
where py >nul 2>nul && set "PY=py -3"
py -3.12 --version >nul 2>nul && set "PY=py -3.12"
echo Creating .venv with: %PY%
%PY% -m venv .venv || goto fail
"%VPY%" -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) and sys.maxsize > 2**32 else 1)"
if errorlevel 1 (
  echo ShortPulse needs 64-bit Python 3.11 or newer. Install 3.12, delete the .venv folder, and run setup again.
  goto fail
)
"%VPY%" -m pip install --upgrade pip || goto fail
"%VPY%" -m pip install -r requirements.txt || goto fail
call :compile || goto fail
echo.
echo Setup finished. Next: .\windows.cmd test
exit /b 0

:build
call :compile || goto fail
echo Built engine\shortpulse.dll
exit /b 0

:test
call :needvenv || exit /b 1
call :compile || goto fail
engine\test_engine.exe || goto fail
pushd tests
"%VPY%" -m unittest -v test_engine test_app test_features test_admin test_concurrency
set "RC=%ERRORLEVEL%"
popd
exit /b %RC%

:run
call :needvenv || exit /b 1
call :compile || goto fail
if not defined SEED_ON_START set "SEED_ON_START=1"
echo Open http://127.0.0.1:5000 in your browser. Stop the server with Ctrl+C.
"%VPY%" -m flask --app wsgi run
exit /b %ERRORLEVEL%

:train
call :needvenv || exit /b 1
"%VPY%" ml\train_models.py
exit /b %ERRORLEVEL%

:compile
where gcc >nul 2>nul
if errorlevel 1 (
  echo gcc was not found on PATH. Install 64-bit MinGW-w64 gcc - see the Windows section of README.md - then open a new terminal.
  exit /b 1
)
gcc -O2 -Wall -Wextra -std=c11 -shared -static-libgcc -o engine\shortpulse.dll engine\shortpulse_engine.c -lbcrypt || exit /b 1
gcc -O2 -Wall -Wextra -std=c11 -static-libgcc -o engine\test_engine.exe engine\test_engine.c engine\shortpulse_engine.c -lbcrypt || exit /b 1
exit /b 0

:needvenv
if exist "%VPY%" exit /b 0
echo No .venv found. Run .\windows.cmd setup first.
exit /b 1

:fail
echo.
echo Something failed. Scroll up to the first error message.
exit /b 1
