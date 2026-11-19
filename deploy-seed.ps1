# Load the demo data into the hosted (Neon) database. Run from server/, with the Test2 backend running on
# port 8002 with MOCK_LINKEDIN=1 (it serves the mock LinkedIn API the seed reads from).
# The connection string is read from .env.deploy (ignored by git) and never printed.
$ErrorActionPreference = 'Stop'
$line = Get-Content .env.deploy | Where-Object { $_ -match '^DATABASE_URL=' } | Select-Object -First 1
if (-not $line) { throw 'Put DATABASE_URL=postgresql://... in server/.env.deploy first' }
$env:DATABASE_URL = $line.Substring('DATABASE_URL='.Length).Trim()
$env:DJANGO_DEBUG = 'true'  # only for this one-off command on the laptop
try {
    .\.venv\Scripts\python.exe manage.py migrate --noinput
    .\.venv\Scripts\python.exe manage.py seed_demo_linkedin --reset
} finally {
    Remove-Item Env:DATABASE_URL
}
