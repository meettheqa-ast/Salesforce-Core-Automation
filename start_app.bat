@echo off
setlocal EnableExtensions
cd /d "%~dp0"

REM Prefer .venv, then venv (same order as common Python tooling)
set "VENV_ACTIVATED=0"
if exist ".venv\Scripts\activate.bat" (
    call ".venv\Scripts\activate.bat"
    set "VENV_ACTIVATED=1"
    goto :run_streamlit
)
if exist "venv\Scripts\activate.bat" (
    call "venv\Scripts\activate.bat"
    set "VENV_ACTIVATED=1"
    goto :run_streamlit
)

echo.
echo [start_app] No virtual environment found (.venv or venv).
echo          Create one, then install dependencies, for example:
echo            py -3 -m venv .venv
echo            .venv\Scripts\activate.bat
echo            pip install -r requirements.txt
echo.
echo [start_app] Trying system Python (py -m streamlit / python -m streamlit^)...
echo.

:run_streamlit
if "%VENV_ACTIVATED%"=="1" (
    streamlit run app.py
) else (
    py -m streamlit run app.py
    if errorlevel 1 python -m streamlit run app.py
)

echo.
echo [start_app] Process ended. Press any key to close this window (read any errors above^).
pause

endlocal
