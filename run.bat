@echo off
rem Start AgentBoard without a console window. First run installs psutil and pywebview if they are missing.
cd /d "%~dp0"
python -c "import psutil, webview" 2>nul || python -m pip install --user -r requirements.txt
start "" pythonw -m agentboard %*
