FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY junyeong-classroom-luxe/requirements-cloud.txt ./
RUN pip install --no-cache-dir -r requirements-cloud.txt
COPY junyeong-classroom-luxe/ ./
COPY oracle-maison-lumiel/patch_oci_interface.py /tmp/patch_oci_interface.py
RUN python brand_build.py && python /tmp/patch_oci_interface.py && python -m compileall -q app.py cloud_app.py
EXPOSE 8000
CMD ["python","-m","uvicorn","cloud_app:app","--host","0.0.0.0","--port","8000","--workers","1","--no-access-log"]
