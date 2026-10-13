@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  echo Python 3.11 이상을 먼저 설치해 주세요. 설치 시 PATH 추가를 선택하세요.
  pause
  exit /b 1
)
if not exist .venv\Scripts\python.exe py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 (pause & exit /b 1)
.venv\Scripts\python.exe -c "from app import db; c=db(); d=c.__enter__(); r=d.execute(\"SELECT 1 FROM members WHERE role='admin'\").fetchone(); c.__exit__(None,None,None); raise SystemExit(0 if r else 3)"
if errorlevel 1 .venv\Scripts\python.exe manage.py create-admin
if errorlevel 1 (pause & exit /b 1)
start "" "http://127.0.0.1:8000"
.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8000 --workers 1
pause
