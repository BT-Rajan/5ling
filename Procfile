api: cd api && .venv/bin/uvicorn app.main:app_factory --factory --reload --reload-dir app --host 127.0.0.1 --port 8000 --no-server-header
web: cd web && npm run dev
mailpit: scripts/optional .dev/bin/mailpit --smtp 127.0.0.1:1025 --listen 127.0.0.1:8025
