@echo off
setlocal enabledelayedexpansion

set "NAME=Playstation1Toolchain-Installer-Windows"
set "ENTRY=main.py"

REM These are used in function-local imports.
set "HIDDEN_IMPORTS=--hidden-import=winreg --hidden-import=msvcrt"

REM For the --deps path, start elevated instead of relying on self-relaunch.
REM Remove --uac-admin if you do not want the exe to request admin at launch.
pyinstaller ^
  --clean ^
  --noconfirm ^
  --onefile ^
  --console ^
  --uac-admin ^
  --icon=Logo.ico ^
  --version-file=version.txt ^
  --name "%NAME%" ^
  %HIDDEN_IMPORTS% ^
  "%ENTRY%"

endlocal