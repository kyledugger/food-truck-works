"""Install the passive probe router into FTW main.py; no network or DB access."""
from pathlib import Path

root = Path(__file__).resolve().parent
target = root / 'main.py'
needle = 'app.include_router(live_dashboard_router)'
addition = ('from routers.transaction_signal_probe import router as transaction_signal_probe_router\n'
            'app.include_router(transaction_signal_probe_router)')
if not (root / 'routers' / 'transaction_signal_probe.py').is_file():
    raise SystemExit('Extract the routers/transaction_signal_probe.py file first.')
with target.open(encoding='utf-8', newline='') as stream:
    text = stream.read()
if 'app.include_router(transaction_signal_probe_router)' in text:
    print('Probe router already installed. No changes.')
elif text.count(needle) != 1:
    raise SystemExit('Expected router anchor not found uniquely; main.py was not changed.')
else:
    backup = root / 'main.py.before_transaction_probe'
    if backup.exists():
        raise SystemExit('Backup already exists; main.py was not changed. Review the prior installation.')
    backup.write_bytes(target.read_bytes())
    newline = '\r\n' if '\r\n' in text else '\n'
    with target.open('w', encoding='utf-8', newline='') as stream:
        stream.write(text.replace(needle, needle + newline + addition.replace('\n', newline)))
    print('Installed passive probe router. Saved main.py.before_transaction_probe.')
