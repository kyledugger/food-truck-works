"""Run from your app root AFTER copying package files: python install_launch_signup.py"""
from pathlib import Path
import re
p = Path("main.py")
s = p.read_text(encoding="utf-8")
if "from routers.launch_updates import" not in s:
    s = s.replace("from routers.auth_routes import router as auth_router", "from routers.launch_updates import router as launch_router, page as launch_page\nfrom routers.auth_routes import router as auth_router")
    s = s.replace("app.include_router(auth_router)", "app.include_router(auth_router)\napp.include_router(launch_router)")
start = s.index('async def root(request: Request):')
end = s.index('@app.get("/dashboard"',start)
block = s[start:end]
block, count = re.subn(r'return templates.TemplateResponse\(.*?\n    \)', 'return launch_page(request)', block, count=1, flags=re.S)
if count != 1 and 'return launch_page(request)' not in block:
    raise SystemExit('Root route differed; no changes written. Add return launch_page(request) manually.')
s = s[:start] + block + s[end:]
compile(s, "main.py", "exec")
if not Path("main.py.before-launch-signup").exists():
    Path("main.py.before-launch-signup").write_bytes(p.read_bytes())
p.write_text(s, encoding="utf-8")
p = Path("alembic/env.py")
s = p.read_text(encoding="utf-8")
if "import launch_models" not in s:
    s = s.replace("import models", "import models\nimport launch_models",1)
p.write_text(s, encoding="utf-8")
print('Router and migration metadata registered. Run alembic upgrade head before starting the app.')
