@echo off
chcp 65001 > nul
title AI Voice Studio PRO - 원클릭 실행기
color 0b

echo =====================================================================
echo       AI Voice Studio PRO (멀티 보이스 오디오 드라마 스튜디오)
echo =====================================================================
echo.
echo [1/3] 파이썬(Python) 환경 확인 중...

where python >nul 2>nul
if %errorlevel% neq 0 (
    color 0c
    echo [오류] 컴퓨터에 Python이 설치되어 있지 않습니다!
    echo https://www.python.org/downloads/ 에서 Python 3.10 또는 3.11을 설치해 주세요.
    echo (설치 시 'Add Python to PATH' 체크박스를 반드시 선택해야 합니다.)
    echo.
    pause
    exit /b
)

echo [2/3] 필수 인공지능 패키지 및 라이브러리 검사 중...
if not exist ".venv" (
    echo [안내] 최초 1회 실행으로 가상환경(.venv)을 생성합니다...
    python -m venv .venv
    call .venv\Scripts\activate.bat
    python -m pip install --upgrade pip
    pip install -r requirements.txt
) else (
    call .venv\Scripts\activate.bat
)

echo [3/3] AI 오디오 스튜디오를 구동합니다...
echo.
echo =====================================================================
echo  💡 브라우저가 자동으로 열립니다. (주소: http://localhost:8501)
echo  💡 이 검은색 창을 닫으시면 프로그램이 종료됩니다.
echo =====================================================================
echo.

timeout /t 2 >nul
start "" http://localhost:8501
streamlit run app.py --server.port 8501

pause