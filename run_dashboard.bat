@echo off
cd /d "%~dp0.."
echo Starting ncode_pipe dashboard at http://127.0.0.1:5005 ...
uv run --with requests --with beautifulsoup4 --with flask --with lxml python -m ncode_pipe.monitor --port 5005
pause
