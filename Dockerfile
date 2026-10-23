FROM node:24-bookworm-slim AS ui
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim
ARG SOURCE_COMMIT=unknown
LABEL org.opencontainers.image.revision=$SOURCE_COMMIT
ENV QF_SOURCE_COMMIT=$SOURCE_COMMIT
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY wallex/ ./wallex/
COPY --from=ui /build/frontend/dist ./frontend/dist
EXPOSE 8080
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s CMD python -B -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=3).close()"
# No network observer unless --fetch-public is explicitly supplied at runtime.
ENTRYPOINT ["python", "-B", "-m", "wallex.infra.observer_api"]
CMD ["--host", "0.0.0.0", "--port", "8080", "--state-dir", "/data/observer", "--frontend-dir", "/app/frontend/dist"]
