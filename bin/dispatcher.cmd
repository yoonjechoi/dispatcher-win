@echo off
rem Entry point for PowerShell and cmd. The program is dispatcher.py.
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
if defined DISPATCHER_PYTHON (
  "%DISPATCHER_PYTHON%" "%~dp0dispatcher.py" %*
  exit /b %ERRORLEVEL%
)
where py >nul 2>nul
if %ERRORLEVEL%==0 (
  py -3 "%~dp0dispatcher.py" %*
) else (
  python "%~dp0dispatcher.py" %*
)
exit /b %ERRORLEVEL%
