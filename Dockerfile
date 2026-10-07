FROM python:3.12.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.lock ./
RUN pip install --no-cache-dir --no-compile -r requirements.lock
COPY pyproject.toml ./
COPY app ./app
COPY scripts ./scripts
COPY .streamlit ./.streamlit
COPY assets ./assets
RUN pip install --no-cache-dir --no-compile --no-deps --no-build-isolation . && \
    rm -rf /app/build /app/filepulse.egg-info && \
    useradd --uid 10001 --create-home filepulse && \
    mkdir -p /app/watched_data/demo && chown -R filepulse:filepulse /app
COPY tests ./tests
COPY migrations ./migrations
USER filepulse
CMD ["python", "-m", "app.collector.server"]
