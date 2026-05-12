@echo off
setlocal

set "ROOT=%~dp0"
set "FAILED=0"

where python >nul 2>nul
if errorlevel 1 (
  echo Python was not found. Install Python or add it to PATH.
  pause
  exit /b 1
)

where npm >nul 2>nul
if errorlevel 1 (
  echo npm was not found. Install Node.js or add npm to PATH.
  pause
  exit /b 1
)

echo [backend] syntax check
pushd "%ROOT%backend" >nul
python -m compileall app
if errorlevel 1 set "FAILED=1"
popd >nul

echo.
if not exist "%ROOT%frontend\node_modules\.bin\next.cmd" (
  echo [frontend] installing dependencies
  pushd "%ROOT%frontend" >nul
  call npm install
  if errorlevel 1 set "FAILED=1"
  popd >nul
  echo.
)

if "%FAILED%"=="1" goto done

echo [frontend] lint
pushd "%ROOT%frontend" >nul
call npm run lint
if errorlevel 1 set "FAILED=1"
popd >nul

echo.
echo [frontend] build
pushd "%ROOT%frontend" >nul
call npm run build
if errorlevel 1 set "FAILED=1"
popd >nul

echo.
:done
if "%FAILED%"=="0" (
  echo All checks passed.
) else (
  echo One or more checks failed.
)

pause
exit /b %FAILED%
