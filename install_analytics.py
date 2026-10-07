"""Run from the app root after copying this patch there. No database migration."""
from pathlib import Path
import re

router = Path('routers/launch_updates.py')
template = Path('templates/coming_soon.html')
r = router.read_text(encoding='utf-8')
t = template.read_text(encoding='utf-8')
marker = 'def analytics_thanks('
if marker not in r:
    old = 'def page(request, message=None, status_code=200):'
    assert old in r, 'Unexpected page helper; no files changed.'
    r = r.replace(old, 'def page(request, message=None, status_code=200, analytics_event=None):', 1)
    old = 'context={"launch_message": message,'
    assert old in r, 'Unexpected template context; no files changed.'
    r = r.replace(old, 'context={"analytics_event": analytics_event, "launch_message": message,', 1)
    outcomes = [
        ('return page(request, "Please check your inbox to confirm your subscription. Thanks for helping shape Food Truck Works!")', 'launch_signup_requested'),
        ('return page(request, "You\'re on the list. Thanks for helping shape Food Truck Works!")', 'launch_signup_confirmed')
    ]
    for old, event in outcomes:
        assert old in r, 'Unexpected success handler; no files changed.'
        r = r.replace(old, 'request.session["launch_analytics_event"] = "' + event + '"\n    return RedirectResponse("/launch-updates/thanks", status_code=303)', 1)
    r = 'from fastapi.responses import RedirectResponse\n' + r if not r.startswith('from __future__') else r
    assert 'from __future__' not in r, 'Move RedirectResponse import after future imports before installing.'
    r += '''\n\n@router.get("/launch-updates/thanks", response_class=HTMLResponse)
def analytics_thanks(request: Request):
    event = request.session.pop("launch_analytics_event", None)
    messages = {
        "launch_signup_requested": "Please check your inbox to confirm your subscription. Thanks for helping shape Food Truck Works!",
        "launch_signup_confirmed": "You're on the list. Thanks for helping shape Food Truck Works!",
    }
    return page(request, messages.get(event), analytics_event=event)
'''
# Remove the previously supplied inline GA snippet, including its staff condition.
pattern = r'{% if not request\.session\.get\("user_id"\) %}\s*<script>.*?G-SSFHN44M6R.*?</script>\s*{% endif %}'
t = re.sub(pattern, '', t, flags=re.S)
if 'data-ftw-analytics' not in t:
    assert 'G-SSFHN44M6R' not in t and 'googletagmanager.com' not in t, 'Remove other Analytics snippets first; no files changed.'
    head = '{% block head %}'
    assert head in t, 'Missing head block; no files changed.'
    t = t.replace(head, head + '''
<script defer src="/static/js/ftw-analytics.js?v=1" data-ftw-analytics
        data-audience="{{ 'signed_in' if request.session.get('user_id') else 'visitor' }}"
        data-event="{{ analytics_event|default('', true) }}"></script>
''', 1)
compile(r, str(router), 'exec')
for path, contents in [(router, r), (template, t)]:
    backup = path.with_name(path.name + '.before-analytics')
    if not backup.exists(): backup.write_bytes(path.read_bytes())
    path.write_text(contents, encoding='utf-8')
print('Analytics installed. No database migration needed.')
