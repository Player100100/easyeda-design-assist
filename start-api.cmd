@echo off
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
where python >nul 2>nul
if errorlevel 1 goto try_py
python -B "%~dp0src\design_assist.py" connect %*
goto done
:try_py
where py >nul 2>nul
if errorlevel 1 goto missing
py -3 -B "%~dp0src\design_assist.py" connect %*
goto done
:missing
echo Python 3.11+ is required. Install Python and reopen this launcher.
set "api_result=2"
goto finish
:done
set "api_result=%errorlevel%"
:finish
if not defined EASYEDA_ASSIST_NO_PAUSE pause
exit /b %api_result%
