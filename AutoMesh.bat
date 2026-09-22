@echo off
rem AutoMesh arayuzunu baslatir. Bu dosyaya cift tiklamaniz yeterli.
rem Depoda .venv varsa onu, yoksa sistemdeki Python'u kullanir.
setlocal
cd /d "%~dp0"

set "PYEXE=py"
if exist ".venv\Scripts\python.exe" set "PYEXE=.venv\Scripts\python.exe"

"%PYEXE%" -m automesh.guiapp
if errorlevel 1 (
    echo.
    echo AutoMesh arayuzu baslatilamadi. Yukaridaki hatayi kontrol edin.
    echo Kurulum icin:  "%PYEXE%" -m pip install -e .
    echo.
    pause
)
endlocal
