ARG PYTHON_IMAGE=python:3.12-slim@sha256:423ed6ab25b1921a477529254bfeeabf5855151dc2c3141699a1bfc852199fbf
FROM ${PYTHON_IMAGE}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY pyproject.toml requirements.lock /app/
COPY backend /app/backend
COPY integrations /app/integrations
COPY references/dean-agent /app/references/dean-agent
RUN pip install --no-cache-dir --constraint requirements.lock setuptools wheel \
    && pip install --no-cache-dir --no-build-isolation --constraint requirements.lock . ./references/dean-agent \
    && pip check
COPY migrations /app/migrations
COPY sources/deanery_db /app/sources/deanery_db
COPY scripts/initialize.py scripts/smoke.py /app/scripts/
RUN useradd --uid 10001 --create-home deanery
USER 10001
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "deanery_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
