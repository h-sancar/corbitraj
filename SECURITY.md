# Security Notes

This repository is intended to be public-safe when committed with the included
`.gitignore`.

Do not commit:
- `.env.local`, `.env`, or any file containing real API keys, secrets, passphrases, or private RPC URLs
- `api.local.txt`, `api.txt`, or personal notes containing credentials
- runtime cache files such as `.coingecko_cache.json`, `.coin_identity_cache.json`, and `.excluded_pairs.json`
- `__pycache__/` or other generated files

Before pushing, run a local secret scan and review `git status --ignored`.

The `/api/save-env` endpoint is a local development helper. It is restricted to
localhost requests in `server.py`; remove it before deploying the app anywhere
publicly reachable.

The server binds to `127.0.0.1` by default through `config.HOST`. Do not expose
it on a public interface while real exchange credentials are configured.
