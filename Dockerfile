FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install --no-cache-dir .
RUN useradd --system --uid 10001 --create-home xyron && mkdir -p /app/data && chown -R xyron:xyron /app
USER xyron

CMD ["python", "-m", "app"]