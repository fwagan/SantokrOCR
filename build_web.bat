@echo off
cd /d "%~dp0"
echo Building Mobile Event Marker Web Server...

:: 激活 conda 环境
call "%USERPROFILE%\miniconda3\condabin\conda.bat" activate base

:: 1) 构建前端（React SPA → web/frontend/dist）
echo --- Building frontend ---
pushd web\frontend
call npm run build
set FRONTEND_EXIT=%errorlevel%
popd
if not %FRONTEND_EXIT% equ 0 (
    echo Frontend build failed!
    exit /b 1
)

:: 2) 打包 Web 后端（onedir → build/web/WebServer/）
echo --- Building backend (PyInstaller) ---
call pyinstaller -y --distpath build\web --workpath build\web_pyi build_web.spec
if %errorlevel% equ 0 (
    echo Done! exe is in build\web\WebServer\
) else (
    echo Build failed!
    pause
)
