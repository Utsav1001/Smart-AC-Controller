@echo off
title Smart AC Controller Launcher
cd /d "%~dp0"

echo ===================================================
echo Starting Smart AC Controller System
echo ===================================================

:: Activate virtual environment
call venv\Scripts\activate.bat

:: 1. Start Mosquitto Broker (if installed in default path)
if exist "C:\Program Files\mosquitto\mosquitto.exe" (
    echo [1/4] Starting Mosquitto MQTT Broker with mosquitto.conf...
    start "Mosquitto Broker" "C:\Program Files\mosquitto\mosquitto.exe" -c "%~dp0mosquitto.conf" -v
    timeout /t 2 /nobreak >nul
) else (
    echo [WARN] Mosquitto not found at default path. Ensure broker is running on localhost:1883.
)

:: 2. Start Backend Service
echo [2/4] Starting Backend Ingestion Service...
start "AC Backend" python backend.py
timeout /t 1 /nobreak >nul

:: 3. Start ESP32 Simulator
echo [3/4] Starting ESP32 Simulator (60x speed)...
start "AC Simulator" python simulator.py --speed 60
timeout /t 1 /nobreak >nul

:: 4. Start Streamlit Dashboard
echo [4/4] Starting Streamlit Live Dashboard...
start "AC Dashboard" python -m streamlit run dashboard.py

echo.
echo All services launched in their respective windows!
echo - Dashboard will open at http://localhost:8501
echo - Run "python check_db.py" anytime to inspect stored rows.
echo ===================================================
pause

