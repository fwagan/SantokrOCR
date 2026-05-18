@echo off
cd /d "%~dp0"
echo Building SantokrOCR...
call pyinstaller -y build.spec
if %errorlevel% equ 0 (
    echo Done! exe is in dist\SantokrOCR\
) else (
    echo Build failed!
    pause
)
