FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements-cloud.txt ./
RUN pip install -r requirements-cloud.txt
COPY . .
RUN python brand_build.py && python -m compileall -q app.py cloud_app.py cloud_storage.py
EXPOSE 8000
CMD ["python","-m","uvicorn","cloud_app:app","--host","0.0.0.0","--port","8000","--workers","1","--no-access-log"]
