#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 - <<'PY'
from pathlib import Path
import secrets
p = Path('.env')
if not p.exists():
    text = Path('.env.example').read_text()
    text = text.replace('JWT_SECRET=\n', f'JWT_SECRET={secrets.token_urlsafe(48)}\n')
    text = text.replace('ADMIN_INITIAL_PASSWORD=\n', f'ADMIN_INITIAL_PASSWORD={secrets.token_urlsafe(18)}\n')
    text = text.replace('DEMO_PASSWORD=\n', f'DEMO_PASSWORD={secrets.token_urlsafe(18)}\n')
    p.write_text(text)
    p.chmod(0o600)
    print('Created .env with random development credentials. Read them locally; do not commit.')
else:
    print('Existing .env preserved.')
PY
