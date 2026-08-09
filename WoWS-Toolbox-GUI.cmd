@echo off
setlocal
set "WOWS_POWERSHELL="
if exist "%ProgramFiles%\PowerShell\7\pwsh.exe" set "WOWS_POWERSHELL=%ProgramFiles%\PowerShell\7\pwsh.exe"
if not defined WOWS_POWERSHELL if exist "%LocalAppData%\Programs\PowerShell\7\pwsh.exe" set "WOWS_POWERSHELL=%LocalAppData%\Programs\PowerShell\7\pwsh.exe"
for /f "delims=" %%P in ('where pwsh.exe 2^>nul') do if not defined WOWS_POWERSHELL set "WOWS_POWERSHELL=%%P"
if not defined WOWS_POWERSHELL if exist "%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" set "WOWS_POWERSHELL=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not defined WOWS_POWERSHELL (
  echo PowerShell 7 or Windows PowerShell 5.1 is required.
  pause
  exit /b 1
)
start "" "%WOWS_POWERSHELL%" -STA -NoLogo -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "%~dp0GUI\Launch-Gui.ps1"
if errorlevel 1 (
  echo Failed to start WoWS Toolbox.
  pause
  exit /b 1
)
endlocal