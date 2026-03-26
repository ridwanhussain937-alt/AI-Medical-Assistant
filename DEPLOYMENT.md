# Deployment Guide

This project is prepared for low-cost and free-tier deployment on Render and Koyeb.

## Render Free

Use the root [`render.yaml`](./render.yaml) blueprint.

What it is configured for:

- free Python web service
- free Render Postgres database
- `/healthz` health check
- production security flags enabled
- pre-deploy migrations, site configuration, and bootstrap

Recommended setup:

1. Push the repository to GitHub.
2. In Render, create a new Blueprint from the repository.
3. Let Render read [`render.yaml`](./render.yaml).
4. Add optional secrets manually if needed:
   - `GROQ_API_KEY`
   - `GOOGLE_OAUTH_CLIENT_ID`
   - `GOOGLE_OAUTH_CLIENT_SECRET`
5. If you want a demo admin account on first deploy, set:
   - `DJANGO_CREATE_DEMO_ADMIN=true`
   - `DJANGO_DEMO_ADMIN_USERNAME=admin1`
   - `DJANGO_DEMO_ADMIN_EMAIL=admin1@example.com`
   - `DJANGO_DEMO_ADMIN_PASSWORD=admin123`

Free-tier limitations:

- the web service sleeps after inactivity
- local uploads and model artifacts are ephemeral
- heavy training jobs are not a good fit

## Koyeb Free

Koyeb can deploy this repository directly from Git using the included [`Dockerfile`](./Dockerfile).

Recommended service settings:

- Builder: `Dockerfile`
- Port: `8000`
- Route: `/:8000`
- Instance: free

Required environment variables:

- `PORT=8000`
- `DJANGO_DEBUG=false`
- `DJANGO_ALLOWED_HOSTS=.koyeb.app`
- `DJANGO_CSRF_TRUSTED_ORIGINS=https://*.koyeb.app`
- `DJANGO_SECRET_KEY=<your-secret>`
- `DATABASE_URL=<your-postgres-connection-string>`
- `DJANGO_SITE_DOMAIN=<your-app>.koyeb.app`
- `DJANGO_SITE_NAME=AI Medical Assistant`
- `DJANGO_USE_X_FORWARDED_HOST=true`
- `DJANGO_SECURE_PROXY_SSL_HEADER=true`
- `DJANGO_SESSION_COOKIE_SECURE=true`
- `DJANGO_CSRF_COOKIE_SECURE=true`
- `DJANGO_SECURE_SSL_REDIRECT=true`
- `DJANGO_SECURE_HSTS_SECONDS=31536000`
- `DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS=true`
- `DJANGO_SECURE_HSTS_PRELOAD=false`
- `DJANGO_SERVE_MEDIA=true`

Optional environment variables:

- `GROQ_API_KEY`
- `GOOGLE_OAUTH_CLIENT_ID`
- `GOOGLE_OAUTH_CLIENT_SECRET`
- `DJANGO_CREATE_DEMO_ADMIN=true`
- `DJANGO_DEMO_ADMIN_USERNAME=admin1`
- `DJANGO_DEMO_ADMIN_EMAIL=admin1@example.com`
- `DJANGO_DEMO_ADMIN_PASSWORD=admin123`

Koyeb note:

- free instances do not give you durable volume-backed storage, so uploads and trained artifacts are best treated as demo-only data

## Docker / VPS

Use [`docker-compose.prod.yml`](./docker-compose.prod.yml) with a real `POSTGRES_PASSWORD` and production host settings in `.env`.
