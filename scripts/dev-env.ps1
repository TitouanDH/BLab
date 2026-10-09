# Dot-source from the repository root to configure a shell for native development:
#   . .\scripts\dev-env.ps1
# Uses the local dev Postgres from docker-compose.dev.yml and fake lab devices.
$env:DJANGO_DEBUG = "1"
$env:DJANGO_SECRET_KEY = "local-development-only"
$env:DJANGO_ALLOWED_HOSTS = "localhost,127.0.0.1"
# The UI posts from Vite's origin, which Django's CSRF check must trust
$env:DJANGO_CSRF_TRUSTED_ORIGINS = "http://localhost:5173,http://127.0.0.1:5173"
$env:DB_ENGINE = "postgresql"
$env:DB_HOST = "127.0.0.1"
$env:DB_PORT = "5433"
$env:DB_NAME = "blab_dev"
$env:DB_USER = "blab_dev"
$env:DB_PASSWORD = "blab_dev_password"
$env:BLAB_DEVICES = "fake"
Write-Host "Dev environment set: local Postgres on 127.0.0.1:5433, BLAB_DEVICES=fake"
