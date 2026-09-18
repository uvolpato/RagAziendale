@echo off
chcp 65001 >nul 2>&1
setlocal
title Assistente aziendale - avvio

REM Avvia l'ambiente di sviluppo. Si puo' lanciare con doppio clic.
REM Tutto il lavoro vero lo fa Sviluppo\avvia.py: qui ci sono solo i
REM controlli preliminari e l'apertura del browser.

cd /d "%~dp0Sviluppo" || goto :errore_cartella

echo ============================================
echo   Assistente aziendale - ambiente di sviluppo
echo ============================================
echo.

REM --- Docker in esecuzione? ----------------------------------------
echo [1/3] Controllo Docker...
docker info >nul 2>&1
if errorlevel 1 (
  echo.
  echo   ERRORE: Docker non risponde.
  echo   Avvia Docker Desktop e attendi che sia pronto, poi rilancia.
  goto :fine_errore
)
echo       ok
echo.

REM --- Python disponibile? ------------------------------------------
echo [2/3] Controllo Python...
py --version >nul 2>&1
if errorlevel 1 (
  echo.
  echo   ERRORE: il comando "py" non e' disponibile.
  echo   Installa Python per Windows dal Microsoft Store o da python.org.
  goto :fine_errore
)
echo       ok
echo.

REM --- Il file di configurazione esiste? ----------------------------
if not exist ".env" (
  echo.
  echo   ERRORE: manca Sviluppo\.env
  echo.
  echo   Copialo dall'esempio e compilalo:
  echo       copy Sviluppo\.env.example Sviluppo\.env
  echo.
  echo   I campi MODELLO_* possono restare vuoti per ora.
  goto :fine_errore
)

REM --- Avvio ---------------------------------------------------------
echo [3/3] Avvio dei servizi...
echo.
py avvia.py
if errorlevel 1 goto :fine_errore

REM --- Browser -------------------------------------------------------
echo.
echo Apro il browser...
for /f "tokens=2 delims==" %%H in ('findstr /b "APP_HOST=" .env') do set APP_HOST=%%H
if "%APP_HOST%"=="" set APP_HOST=assistente.localhost
start "" "https://%APP_HOST%"

echo.
echo ============================================
echo   Pronto.
echo.
echo   Per fermare tutto:  ferma.cmd
echo   Per lo stato:       py Sviluppo\avvia.py --stato
echo ============================================
echo.
echo Questa finestra si puo' chiudere.
ping -n 16 127.0.0.1 >nul
exit /b 0

:errore_cartella
echo   ERRORE: non trovo la cartella Sviluppo accanto a questo file.
echo   Lascia avvia.cmd nella radice del progetto.

:fine_errore
echo.
echo ------------------------------------------------
echo   Avvio NON completato. Vedi il messaggio sopra.
echo ------------------------------------------------
echo.
pause
exit /b 1
