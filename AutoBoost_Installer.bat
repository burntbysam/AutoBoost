@echo off
setlocal EnableExtensions
rem ============================================================
rem  AutoBoost installer / updater -- per-user, no admin rights.
rem  Safe to re-run: fresh install on a new machine, update on a
rem  machine that already has AutoBoost. See README.md.
rem
rem  NOTE: written off-workstation; on-workstation validation of
rem  this script is pending.
rem ============================================================

set "BRANCH=claude/autoboost-handoff-ei9bk3"
set "REPO_URL=https://github.com/burntbysam/AutoBoost.git"
set "PY_CMD=py"
set "AB_VERSION=unknown"
set "UPDATE_WARN="
set "PIP_WARN="

echo ============================================================
echo  AutoBoost installer / updater
echo ============================================================
echo This will, entirely per-user (no admin rights needed):
echo   1. Check that Python (the 'py' launcher) and git are installed
echo   2. Update this AutoBoost clone -- or, if run standalone, clone
echo      a fresh one into "%USERPROFILE%\AutoBoost"
echo   3. Install/upgrade the Python dependencies (pip --user)
echo   4. Create a Desktop shortcut "AutoBoost" (best-effort)
echo.

rem --- [1/4] prerequisites ------------------------------------
echo [1/4] Checking prerequisites...
py --version >nul 2>&1
if not errorlevel 1 goto :have_python
python --version >nul 2>&1
if errorlevel 1 goto :fail_no_python
set "PY_CMD=python"
:have_python
echo   - Python found (%PY_CMD%)
git --version >nul 2>&1
if errorlevel 1 goto :fail_no_git
echo   - git found
echo.

rem --- [2/4] locate or obtain the repo ------------------------
rem If this .bat sits inside the AutoBoost clone, operate on that
rem clone; otherwise clone into %USERPROFILE%\AutoBoost.
set "REPO_DIR="
for /f "delims=" %%i in ('git -C "%~dp0." rev-parse --show-toplevel 2^>nul') do set "REPO_DIR=%%i"
if not defined REPO_DIR goto :standalone
set "REPO_DIR=%REPO_DIR:/=\%"
if exist "%REPO_DIR%\autoboost\__init__.py" goto :update_clone
set "REPO_DIR="

:standalone
set "REPO_DIR=%USERPROFILE%\AutoBoost"
if exist "%REPO_DIR%\.git" goto :update_clone
if exist "%REPO_DIR%" goto :fail_dir_exists
echo [2/4] No existing install found -- cloning AutoBoost into:
echo         "%REPO_DIR%"
git clone --branch "%BRANCH%" "%REPO_URL%" "%REPO_DIR%"
if errorlevel 1 goto :fail_clone
goto :deps

:update_clone
echo [2/4] Updating the existing clone at "%REPO_DIR%"...
git -C "%REPO_DIR%" fetch origin "%BRANCH%"
if errorlevel 1 goto :warn_fetch
git -C "%REPO_DIR%" merge --ff-only "origin/%BRANCH%"
if errorlevel 1 goto :warn_merge
echo   - code is up to date with origin/%BRANCH%
goto :deps

:warn_fetch
echo [WARN] Could not fetch updates -- offline, or git cannot reach GitHub.
echo        Continuing with the version already on disk.
set "UPDATE_WARN=1"
goto :deps

:warn_merge
echo [WARN] Update fetched but NOT applied: this clone has local changes or
echo        has diverged, so a fast-forward is not possible. Your local work
echo        was left untouched. Commit, stash, or revert it, then re-run
echo        this installer to update.
set "UPDATE_WARN=1"
goto :deps

rem --- [3/4] dependencies -------------------------------------
:deps
echo.
echo [3/4] Installing/upgrading Python dependencies (pip --user)...
%PY_CMD% -m pip install --user --upgrade -r "%REPO_DIR%\requirements.txt"
if not errorlevel 1 goto :shortcut
%PY_CMD% -c "import pyautogui, cv2, numpy, PIL, pywinauto" >nul 2>&1
if errorlevel 1 goto :fail_pip
echo [WARN] pip could not install/upgrade -- offline, or a proxy issue --
echo        but all required packages are already installed. AutoBoost can
echo        run with the packages you have; re-run this installer later to
echo        pick up dependency upgrades.
set "PIP_WARN=1"

rem --- [4/4] Desktop shortcut (best-effort) -------------------
:shortcut
echo.
echo [4/4] Creating the Desktop shortcut (best-effort)...
set "AUTOBOOST_DIR=%REPO_DIR%"
set "AUTOBOOST_PY=%PY_CMD%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $pyw = (Get-Command pyw.exe -ErrorAction SilentlyContinue).Source; if (-not $pyw) { $exe = (& $env:AUTOBOOST_PY -c 'import sys;print(sys.executable)' 2>$null); if ($exe) { $cand = Join-Path (Split-Path -Parent ([string]$exe).Trim()) 'pythonw.exe'; if (Test-Path $cand) { $pyw = $cand } } }; if (-not $pyw) { $pyw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source }; if (-not $pyw) { exit 1 }; $desktop = [Environment]::GetFolderPath('Desktop'); $ws = New-Object -ComObject WScript.Shell; $lnk = $ws.CreateShortcut((Join-Path $desktop 'AutoBoost.lnk')); $lnk.TargetPath = $pyw; $lnk.Arguments = '-m autoboost.gui'; $lnk.WorkingDirectory = $env:AUTOBOOST_DIR; $lnk.Description = 'AutoBoost control panel'; $lnk.Save(); exit 0 } catch { exit 1 }"
if errorlevel 1 goto :warn_shortcut
echo   - shortcut "AutoBoost" created on the Desktop
goto :summary

:warn_shortcut
echo [WARN] Could not create the Desktop shortcut. Not a problem -- launch
echo        AutoBoost manually as shown below.
goto :summary

rem --- summary -------------------------------------------------
:summary
for /f "tokens=2 delims== " %%v in ('findstr /B /C:"__version__" "%REPO_DIR%\autoboost\__init__.py"') do set "AB_VERSION=%%~v"
echo.
echo ============================================================
echo  SUCCESS -- AutoBoost %AB_VERSION% is ready
echo ============================================================
echo   Location:  %REPO_DIR%
echo   Launch:    double-click the "AutoBoost" Desktop shortcut, or run:
echo                cd /d "%REPO_DIR%"
echo                %PY_CMD% -m autoboost.gui
if defined UPDATE_WARN echo   NOTE: the code update was not applied -- see the warning above.
if defined PIP_WARN echo   NOTE: the dependency upgrade was skipped -- see the warning above.
echo.
echo Re-run this installer any time to update AutoBoost.
pause
exit /b 0

rem --- failure exits -------------------------------------------
:fail_no_python
echo.
echo [FAIL] Python was not found -- neither the 'py' launcher nor 'python'
echo        is available. This installer cannot install Python without
echo        admin rights. Install Python 3.x from your company software
echo        portal (enable "Add python.exe to PATH" / the py launcher),
echo        then re-run this installer.
goto :fail

:fail_no_git
echo.
echo [FAIL] git was not found. This installer cannot install git without
echo        admin rights. Install "Git for Windows" from your company
echo        software portal, then re-run this installer.
goto :fail

:fail_dir_exists
echo.
echo [FAIL] "%REPO_DIR%" already exists but is not a
echo        git clone of AutoBoost. To avoid overwriting anything, nothing
echo        was touched. Move or rename that folder, then re-run this
echo        installer.
goto :fail

:fail_clone
echo.
echo [FAIL] git clone failed -- check your network connection and that you
echo        can reach https://github.com/burntbysam/AutoBoost, then re-run
echo        this installer.
goto :fail

:fail_pip
echo.
echo [FAIL] pip could not install the dependencies and they are not already
echo        present, so AutoBoost cannot run yet. Check your network/proxy,
echo        then re-run this installer. Manual fallback, from "%REPO_DIR%":
echo          %PY_CMD% -m pip install --user -r requirements.txt
goto :fail

:fail
echo.
echo ============================================================
echo  AutoBoost install FAILED -- see the message above.
echo ============================================================
pause
exit /b 1
