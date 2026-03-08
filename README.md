# Greek Chapters CRM

Light-blue themed Flask CRM for exploring chapters/vendors and managing private pipeline data (prospect/served, tasks, notes, timeline, dashboard KPIs).

## Tech Stack

- Python 3.12+
- Flask
- SQLite
- Alpine.js + Tailwind (CDN)

## Local Setup

1. Create and activate a virtual environment.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Run tests:

```bash
python -m unittest -v test_app.py
```

4. Start app:

```bash
python run.py
```

App runs on `http://127.0.0.1:5000` by default.

## Environment Variables

Copy `.env.example` values into your environment:

- `FLASK_SECRET_KEY` (required for production)
- `FLASK_ENV` (`production` for production checks)
- `FLASK_DEBUG` (`0` in production)
- `FLASK_RUN_HOST` (default `127.0.0.1`)
- `FLASK_RUN_PORT` (default `5000`)
- SMTP (optional):
  - `SMTP_HOST`
  - `SMTP_PORT`
  - `SMTP_USER`
  - `SMTP_PASS`
  - `SMTP_FROM`

## Public Release Checklist

- [x] Debug mode is env-controlled (not hardcoded on)
- [x] Session cookie security defaults set
- [x] `.gitignore` added for local env/cache/db artifacts
- [x] Unit tests passing
- [ ] Add your license file (MIT/Apache/etc.) before publishing

## Push To GitHub

```bash
git add .
git commit -m "Release-ready public version"
git branch -M main
git remote add origin <your-repo-url>
git push -u origin main
```
