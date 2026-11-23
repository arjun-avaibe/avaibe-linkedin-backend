# Load the demo data into the hosted (Neon) database. Run from server/, with the Test2 backend running on
# port 8002 with MOCK_LINKEDIN=1 (it serves the mock LinkedIn API the seed reads from).
# The connection string is read from .env.deploy (ignored by git) and never printed.
# Loading over the internet takes several minutes.
$ErrorActionPreference = 'Stop'
$line = Get-Content .env.deploy | Where-Object { $_ -match '^DATABASE_URL=' } | Select-Object -First 1
if (-not $line) { throw 'Put DATABASE_URL=postgresql://... in server/.env.deploy first' }
$env:DATABASE_URL = $line.Substring('DATABASE_URL='.Length).Trim()
$env:DJANGO_DEBUG = 'true'  # only for this one-off command on the laptop
# Python writes its progress lines to stderr; Windows PowerShell would treat each one as an error and stop.
# So stderr is allowed here, and success is judged by each command's exit code instead.
$ErrorActionPreference = 'Continue'
try {
    .\.venv\Scripts\python.exe manage.py migrate --noinput 2>&1 | ForEach-Object { "$_" }
    if ($LASTEXITCODE -ne 0) { throw 'migrate failed' }
    .\.venv\Scripts\python.exe manage.py seed_demo_linkedin --reset 2>&1 | ForEach-Object { "$_" } | Where-Object { $_ -notmatch 'HTTP Request:' }
    if ($LASTEXITCODE -ne 0) { throw 'loading the demo data failed' }
    Write-Host 'Demo data loaded.' -ForegroundColor Green
} finally {
    Remove-Item Env:DATABASE_URL
    Remove-Item Env:DJANGO_DEBUG
}
