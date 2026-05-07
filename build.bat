@echo off
setlocal enabledelayedexpansion

echo ============================================
echo   MT5 Copy Manual - Build Script
echo ============================================
echo.

:: Verificar Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python no encontrado en PATH.
    echo         Instala Python desde https://python.org
    pause & exit /b 1
)
for /f "tokens=*" %%v in ('python --version') do echo [OK] %%v

:: -----------------------------------------------
:: [1/3] Verificar / instalar dependencias
:: -----------------------------------------------
echo.
echo [1/3] Verificando dependencias de build...

:: Comprobar si PyInstaller ya esta instalado
python -m PyInstaller --version >nul 2>&1
if %errorlevel% equ 0 (
    for /f "tokens=*" %%v in ('python -m PyInstaller --version') do echo [OK] PyInstaller %%v ya instalado.
) else (
    echo [INFO] PyInstaller no encontrado, instalando...
    python -m pip install pyinstaller
    if %errorlevel% neq 0 (
        echo [WARN] pip install fallo. Comprobando si PyInstaller es usable igualmente...
        python -m PyInstaller --version >nul 2>&1
        if %errorlevel% neq 0 (
            echo [ERROR] PyInstaller no disponible. Cierra todos los programas Python y vuelve a intentarlo.
            pause & exit /b 1
        )
        echo [OK] PyInstaller usable pese al error de pip.
    )
)

:: Instalar requirements del proyecto
python -m pip install -r requirements.txt --quiet
if %errorlevel% neq 0 (
    echo [WARN] Alguna dependencia de requirements.txt fallo. El build puede continuar si ya estaban instaladas.
)
echo [OK] Dependencias listas.

:: -----------------------------------------------
:: [2/3] Compilar con PyInstaller
:: -----------------------------------------------
echo.
echo [2/3] Compilando con PyInstaller...
python -m PyInstaller mt5_copy.spec --clean --noconfirm
if %errorlevel% neq 0 (
    echo [ERROR] Fallo la compilacion con PyInstaller.
    pause & exit /b 1
)
echo [OK] Ejecutable generado en: dist\MT5CopyManual\MT5CopyManual.exe

:: -----------------------------------------------
:: [3/3] Compilar instalador con Inno Setup
:: -----------------------------------------------
echo.
echo [3/3] Buscando Inno Setup...

set ISCC=
if exist "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" set "ISCC=C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if exist "C:\Program Files\Inno Setup 6\ISCC.exe"       set "ISCC=C:\Program Files\Inno Setup 6\ISCC.exe"

if "%ISCC%"=="" (
    echo [INFO] Inno Setup no encontrado.
    echo.
    echo        Para generar el instalador .exe:
    echo        1. Descarga Inno Setup desde https://jrsoftware.org/isdl.php
    echo        2. Instalalo y vuelve a ejecutar este script.
    echo        3. O abre installer.iss directamente con el IDE de Inno Setup.
    echo.
) else (
    echo [OK] Inno Setup encontrado.
    "%ISCC%" installer.iss
    if exist "dist\MT5CopyManual_Setup_1.0.0.exe" (
        echo [OK] Instalador generado en: dist\MT5CopyManual_Setup_1.0.0.exe
    ) else (
        echo [ERROR] Fallo la compilacion del instalador. Revisa los mensajes de ISCC arriba.
        pause & exit /b 1
    )
)

echo.
echo ============================================
echo   Build completado correctamente
echo ============================================
echo.
pause
