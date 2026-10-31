FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements-runtime.txt .
RUN pip install --no-cache-dir -r requirements-runtime.txt
COPY . .
RUN python manage.py collectstatic --noinput
RUN useradd --create-home app && mkdir -p staticfiles && chown app:app staticfiles
USER app
EXPOSE 8000
CMD ["sh", "-c", "exec gunicorn config.wsgi:application -b 0.0.0.0:${PORT:-8000} -w 1 --threads 4 --access-logfile -"]
