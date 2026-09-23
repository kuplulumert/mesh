@echo off
rem =====================================================================
rem  AutoMesh baslatici.  Cift tiklayin, baska hicbir sey gerekmez.
rem
rem  Kullanim:
rem    AutoMesh.bat            arayuzu ac (konsol penceresi kapanir)
rem    AutoMesh.bat konsol     arayuzu ac, gunlugu konsolda da goster
rem    AutoMesh.bat doctor     ortam kontrolu
rem    (geometriyi dosyanin/kisayolun uzerine surukleyip birakabilirsiniz)
rem    AutoMesh.bat run x.scdoc --cores 8    komut satiri
rem
rem  Not: bu dosyadaki mesajlar bilerek ASCII. Konsolun kod sayfasi ne
rem  olursa olsun okunur kalsinlar diye Turkce karakter kullanilmadi.
rem =====================================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"
title AutoMesh

set "ACTION=%~1"

rem ---------------------------------------------------------------- 1/4
rem Calisan bir Python bul. Once AUTOMESH_PYTHON, sonra py, sonra python.
rem Bulunan yorumlayici gercekten calisiyor mu diye sinanir: Microsoft
rem Store'un "python" kisayolu PATH'te gorunur ama Python degildir.
set "PYEXE="
set "PYWEXE="
if defined AUTOMESH_PYTHON call :try_python "%AUTOMESH_PYTHON%" ""
call :try_python "py" "pyw"
call :try_python "python" "pythonw"
if not defined PYEXE goto :no_python

rem ---------------------------------------------------------------- 2/4
rem Modul yollari. Depodaki src her zaman basa gelir; ek klasorler
rem (ornegin PyFluent'in kurulu oldugu yer) automesh-yollar.txt icinde
rem her satirda bir tane yazilir.
set "EXTRA=%~dp0src"
if exist "%~dp0automesh-yollar.txt" (
    for /f "usebackq eol=# delims=" %%L in ("%~dp0automesh-yollar.txt") do (
        set "LINE=%%L"
        if defined LINE set "EXTRA=!EXTRA!;!LINE!"
    )
)
if defined PYTHONPATH (
    set "PYTHONPATH=!EXTRA!;!PYTHONPATH!"
) else (
    set "PYTHONPATH=!EXTRA!"
)

rem ---------------------------------------------------------------- 3/4
rem Kisayolun uzerine bir geometri surukleyip birakildiysa arayuz o
rem dosyayla acilir.
set "DROPPED="
if not "%ACTION%"=="" (
    if exist "%ACTION%" set "DROPPED=%ACTION%"
)
if defined DROPPED goto :gui
if /i "%ACTION%"=="" goto :gui
if /i "%ACTION%"=="gui" goto :gui
if /i "%ACTION%"=="konsol" goto :gui_console
goto :cli

rem ---------------------------------------------------------------- 4/4
:gui
rem Penceresiz baslatmadan once import'lari sina: pyw ile baslatilan bir
rem hata hicbir yerde gorunmez, burada ise ekrana yazilir.
call :preflight
if errorlevel 1 goto :import_error
if defined PYWEXE (
    if defined DROPPED (
        start "" "%PYWEXE%" -m automesh.guiapp "!DROPPED!"
    ) else (
        start "" "%PYWEXE%" -m automesh.guiapp
    )
    goto :fin
)
"%PYEXE%" -m automesh.guiapp "!DROPPED!"
if errorlevel 1 goto :run_error
goto :fin

:gui_console
call :preflight
if errorlevel 1 goto :import_error
"%PYEXE%" -m automesh.guiapp
echo.
echo Arayuz kapandi.
pause
goto :fin

:cli
"%PYEXE%" -m automesh %*
echo.
pause
goto :fin

rem ---------------------------------------------------------------------
rem yardimci bloklar
rem ---------------------------------------------------------------------
:try_python
rem %1 = yorumlayici, %2 = penceresiz esi (varsa)
if defined PYEXE goto :eof
if "%~1"=="" goto :eof
rem Tam yol verilmisse dosya olarak, aksi halde PATH icinde aranir.
if exist "%~1" goto :try_run
where "%~1" >nul 2>&1
if errorlevel 1 goto :eof
:try_run
rem 7 ile cikabiliyorsa gercekten Python'dur
"%~1" -c "import sys; sys.exit(7)" >nul 2>&1
if not errorlevel 7 goto :eof
if errorlevel 8 goto :eof
set "PYEXE=%~1"
if "%~2"=="" goto :eof
where "%~2" >nul 2>&1
if errorlevel 1 goto :eof
set "PYWEXE=%~2"
goto :eof

:preflight
set "PFLOG=%TEMP%\automesh-onkontrol.txt"
"%PYEXE%" -c "import automesh.guiapp.app" 2>"%PFLOG%"
exit /b %errorlevel%

:no_python
echo.
echo AutoMesh: calisan bir Python bulunamadi.
echo.
echo   - Komut isteminde "py -V" yazip deneyin.
echo   - Python baska bir klasorde ise yolunu AUTOMESH_PYTHON
echo     degiskeniyle verin, sonra bu dosyayi yeniden calistirin:
echo         setx AUTOMESH_PYTHON "D:\Python311\python.exe"
echo.
pause
goto :fin

:import_error
echo.
echo AutoMesh baslatilamadi. Hata:
echo.
if exist "%PFLOG%" type "%PFLOG%"
echo.
echo Ne yapmali:
echo   1) Ortami kontrol edin:   AutoMesh.bat doctor
echo   2) PyFluent baska bir klasordeyse o klasoru automesh-yollar.txt
echo      dosyasina ekleyin (ornek: automesh-yollar.ornek.txt).
echo   3) Tkinter eksikse python.org kurulumunu "Modify" ile onarip
echo      "tcl/tk and IDLE" kutusunu isaretleyin.
echo.
pause
goto :fin

:run_error
echo.
echo AutoMesh beklenmedik sekilde kapandi. Yukaridaki hatayi kontrol edin.
echo Ayrintili gunluk:  %TEMP%\automesh-hata.txt
echo.
pause
goto :fin

:fin
endlocal
