@echo off
setlocal enabledelayedexpansion

set "NAME=Playstation1Toolchain-Installer-Windows"
set "ENTRY=main.py"

REM These are imported inside functions, so include them explicitly.
set "PYI_OPTS=--hidden-import=winreg --hidden-import=msvcrt"

pyinstaller --clean --noconfirm --onefile --console --debug=imports --name "%NAME%" %PYI_OPTS% "%ENTRY%"

endlocal