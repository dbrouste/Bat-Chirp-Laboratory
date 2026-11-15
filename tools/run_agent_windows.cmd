@echo off
setlocal
cd /d "%~dp0.."
if not defined CHIRP_DATASET set "CHIRP_DATASET=%USERPROFILE%\Documents\Python\Python-data\dataset-v1"
if not defined CHIRP_AGENT_OUTPUT set "CHIRP_AGENT_OUTPUT=%USERPROFILE%\Documents\Python\Python-data\chirp-agent-v1"
echo.
echo === Bat-Chirp-Laboratory : agent local ===
echo Dataset : %CHIRP_DATASET%
echo Resultats : %CHIRP_AGENT_OUTPUT%
echo.
python -m laboratory.autonomous_optimizer --dataset "%CHIRP_DATASET%" --out "%CHIRP_AGENT_OUTPUT%" --workers 4 --max-total 11 %*
set "EXIT_CODE=%ERRORLEVEL%"
echo.
if "%EXIT_CODE%"=="0" if exist "%CHIRP_AGENT_OUTPUT%\agent_report.html" start "" "%CHIRP_AGENT_OUTPUT%\agent_report.html"
echo Code retour : %EXIT_CODE%
echo.
pause
exit /b %EXIT_CODE%
