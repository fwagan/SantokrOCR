@echo off
cd /d "%~dp0"
echo Building SantokrOCR...

:: 激活 conda 环境
call "%USERPROFILE%\miniconda3\condabin\conda.bat" activate base

call pyinstaller -y build.spec
if %errorlevel% equ 0 (
    :: 复制 Web 服务器产物到主程序目录（固定路径 build/web/WebServer，供 app 自动启动）
    if exist build\web\WebServer (
        echo --- Copying Web Server into dist ---
        xcopy /E /I /Y build\web\WebServer dist\SantokrOCR\build\web\WebServer >nul
    )
    :: 复制默认 Web 配置模板到主程序旁 config（配置链搜索 app_base/config，随附避免"读不到配置"报错）
    if not exist dist\SantokrOCR\config mkdir dist\SantokrOCR\config
    copy /Y config\web_config.yaml.example dist\SantokrOCR\config\ >nul
    echo Done! exe is in dist\SantokrOCR\
) else (
    echo Build failed!
    pause
)
