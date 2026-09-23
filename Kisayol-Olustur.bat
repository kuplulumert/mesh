@echo off
rem =====================================================================
rem  Masaustune ve Baslat menusune AutoMesh kisayolu koyar.
rem  Tek sefer calistirmaniz yeterli; sonra sadece kisayola cift tiklarsiniz.
rem  Hicbir sey kurmaz, sadece iki .lnk dosyasi olusturur.
rem =====================================================================
setlocal
cd /d "%~dp0"

set "AM_TARGET=%~dp0AutoMesh.bat"
set "AM_HOME=%~dp0"
set "AM_ICON=%SystemRoot%\System32\shell32.dll,15"

if not exist "%AM_TARGET%" (
    echo AutoMesh.bat bu klasorde bulunamadi: %~dp0
    pause
    exit /b 1
)

echo Kisayollar olusturuluyor...
powershell -NoProfile -Command "$w=New-Object -ComObject WScript.Shell; foreach($f in @('Desktop','Programs')){ $d=[Environment]::GetFolderPath($f); if(-not $d){continue}; $p=Join-Path $d 'AutoMesh.lnk'; $s=$w.CreateShortcut($p); $s.TargetPath=$env:AM_TARGET; $s.WorkingDirectory=$env:AM_HOME; $s.IconLocation=$env:AM_ICON; $s.Description='AutoMesh - otonom Fluent meshing'; $s.Save(); Write-Host ('  ' + $p) }"
if errorlevel 1 goto :manual

echo.
echo Hazir. Masaustundeki AutoMesh kisayoluna cift tiklayabilirsiniz.
echo Baslat menusune de eklendi: "AutoMesh" yazarak aratabilirsiniz.
echo Gorev cubugu icin kisayola sag tiklayip "Gorev cubuguna sabitle" deyin.
echo.
pause
exit /b 0

:manual
echo.
echo Kisayol otomatik olusturulamadi (PowerShell kisitli olabilir).
echo Elle yapmak icin:
echo   1) AutoMesh.bat dosyasina SAG tiklayin
echo   2) "Kisayol olustur" deyin
echo   3) Olusan kisayolu masaustune surukleyin
echo.
pause
exit /b 1
