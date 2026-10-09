netstat -ano | findstr :8000
if ($LASTEXITCODE -eq 1) {
    $env:DOTENV_FILE = ".env.local-prod-db"
    uvicorn main:app --host 0.0.0.0 --port 8000 --reload
} else {
    "Failed Startup: There is a process running on port 8000 already"
}