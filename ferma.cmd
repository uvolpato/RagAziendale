@echo off
chcp 65001 >nul 2>&1
setlocal
title Assistente aziendale - arresto

cd /d "%~dp0Sviluppo" || (
  echo ERRORE: non trovo la cartella Sviluppo accanto a questo file.
  pause
  exit /b 1
)

echo Fermo i servizi...
echo.
docker compose down
echo.
echo Fermati. I dati restano nei volumi: al prossimo avvio si ritrova tutto.
echo.
echo Per cancellare ANCHE il database e ripartire da zero:
echo     cd Sviluppo
echo     docker compose down -v
echo.
ping -n 13 127.0.0.1 >nul
