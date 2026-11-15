@echo off
setlocal
cd /d "%~dp0.."
if not defined CHIRP_DATASET set "CHIRP_DATASET=%USERPROFILE%\Documents\Python\Python-data\dataset-v1"
if not defined CHIRP_AGENT_OUTPUT set "CHIRP_AGENT_OUTPUT=%USERPROFILE%\Documents\Python\Python-data\chirp-agent-v1"
if not defined CHIRP_CODE_AGENT_OUTPUT set "CHIRP_CODE_AGENT_OUTPUT=%USERPROFILE%\Documents\Python\Python-data\chirp-code-agent-v1"
echo.
echo === Chirp Lab : Codex code agent pilot ===
echo Calibrage : %CHIRP_AGENT_OUTPUT%\agent_summary.json
echo Sortie : %CHIRP_CODE_AGENT_OUTPUT%
echo.
where codex >nul 2>nul
if errorlevel 1 (
  echo Codex CLI manquant. Installe-le et lance codex login.
  pause
  exit /b 2
)
if not exist "%CHIRP_AGENT_OUTPUT%\agent_summary.json" (
  echo Agent de parametres non termine. Executer run_agent_windows.cmd.
  pause
  exit /b 2
)
python -m laboratory.code_agent ^
  --dataset "%CHIRP_DATASET%" ^
  --parameter-agent-summary "%CHIRP_AGENT_OUTPUT%\agent_summary.json" ^
  --out "%CHIRP_CODE_AGENT_OUTPUT%" ^
  --workers 4 --max-total 3 %*
set "EXIT_CODE=%ERRORLEVEL%"
echo.
echo Rapport : %CHIRP_CODE_AGENT_OUTPUT%\code_agent_summary.json
echo Code retour : %EXIT_CODE%
pause
exit /b %EXIT_CODE%
