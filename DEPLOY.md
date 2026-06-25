# HirePilot — Deployment Guide

## Local development

```bash
# 1. Clone and install
git clone https://github.com/yourorg/hirepilot.git
cd hirepilot
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Set environment variables
cp .env.example .env
# Edit .env — set HIREPILOT_SECRET_KEY to a random 32+ char string:
python3 -c "import secrets; print(secrets.token_urlsafe(32))"

# 3. Run
streamlit run app.py
```

---

## Docker (local or any VPS)

```bash
# Build
docker build -t hirepilot .

# Run (replace SECRET_KEY with your actual key)
docker run -d \
  -p 8501:8501 \
  -e HIREPILOT_SECRET_KEY="your-secret-key-here" \
  -v hirepilot_data:/data \
  --name hirepilot \
  hirepilot

# Or with docker-compose (reads from .env file)
docker-compose up -d
```

The database is stored at `/data/hirepilot.db` inside the container, mounted as a Docker volume so it persists across restarts and deploys.

---

## Render (recommended — $7/month)

1. Push your repo to GitHub
2. Go to [render.com](https://render.com) → New → Web Service
3. Connect your GitHub repo
4. Render detects `render.yaml` automatically
5. Set the environment variable in Render dashboard:
   - `HIREPILOT_SECRET_KEY` = your secret key
6. Deploy

The `render.yaml` in this repo configures:
- Python runtime (3.11)
- Streamlit start command
- 1GB persistent disk at `/data` for SQLite
- Health check at `/_stcore/health`

**Generate your secret key:**
```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

---

## Railway

```bash
# Install Railway CLI
npm install -g @railway/cli

# Login and initialize
railway login
railway init

# Set secrets
railway variables set HIREPILOT_SECRET_KEY="$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")"
railway variables set HIREPILOT_DB_PATH=/app/data/hirepilot.db

# Deploy
railway up
```

Railway uses `railway.toml` for build/start configuration. Add a Railway volume mount at `/app/data` for SQLite persistence.

---

## Supabase PostgreSQL (Phase 3)

For production scale (500+ users), migrate from SQLite to PostgreSQL:

```bash
# 1. Create a Supabase project at supabase.com
# 2. Copy your project's DATABASE_URL from Supabase dashboard
# 3. Run migrations
psql "$DATABASE_URL" < supabase/migrations/001_initial_schema.sql
psql "$DATABASE_URL" < supabase/migrations/002_rls_service_role_bypass.sql
```

The schema in `supabase/migrations/` mirrors the SQLite schema with:
- UUID primary keys (instead of integer)
- TIMESTAMPTZ (instead of TEXT)
- Row-level security (RLS) policies
- GIN trigram indexes for company/role search
- `updated_at` trigger for the jobs table

---

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `HIREPILOT_SECRET_KEY` | **Yes** | 32+ char random string for encrypting API keys in DB |
| `HIREPILOT_DB_PATH` | No | SQLite file path (default: `./hirepilot.db`) |
| `LOG_LEVEL` | No | Python logging level (default: `INFO`) |

**Never commit `HIREPILOT_SECRET_KEY` to git.** If it leaks, all stored API keys must be reset.

---

## CI/CD

GitHub Actions workflows in `.github/workflows/`:

| Workflow | Trigger | What it does |
|----------|---------|-------------|
| `ci.yml` | Push to any branch | Tests, lint, security audit, Docker build verification |
| `deploy.yml` | Push to `main` | Triggers Render/Railway deploy hook |

**Required GitHub repository secrets:**
- `RENDER_DEPLOY_HOOK_URL` — from Render dashboard → service → Settings → Deploy hooks
- `RAILWAY_TOKEN` — from Railway dashboard → account → API tokens

---

## Running tests

```bash
pip install pytest pytest-cov
pytest tests/ -v --cov=. --cov-report=term-missing
```

Or run without pytest:
```bash
HIREPILOT_SECRET_KEY=test python3 tests/test_auth.py
HIREPILOT_SECRET_KEY=test python3 tests/test_database.py
```

---

## Health check

Streamlit exposes a health endpoint at `/_stcore/health`.

```bash
curl http://localhost:8501/_stcore/health
# Returns: "ok"
```

Used by Docker HEALTHCHECK, Render, and Railway readiness probes.
