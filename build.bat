@echo off
cd /d "%~dp0"
echo Building SantokrOCR...

:: 激活 conda 环境
call "%USERPROFILE%\miniconda3\condabin\conda.bat" activate base

call pyinstaller -y build.spec
if %errorlevel% equ 0 (
    echo Done! exe is in dist\SantokrOCR\
) else (
    echo Build failed!
    pause
)
