# API contracts

`openapi.json` is generated from FastAPI and is the wire contract shared by the web client and integration tooling. Runtime validation lives in `apps/backend/aho/schemas.py`; the web presentation types are in `apps/admin-web/src/types.ts`.

Regenerate after API changes:

```sh
cd apps/backend
../../.venv/bin/python -c 'import json; from pathlib import Path; from aho.api import app; Path("../../packages/shared-types/openapi.json").write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2))'
```

The bot calls the same domain services directly; it does not maintain a second state machine for ticket status.
