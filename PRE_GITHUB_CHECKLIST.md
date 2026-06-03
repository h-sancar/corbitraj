# Pre-GitHub Checklist

Use this before creating the first public commit.

## Required

- Confirm `.env.local`, `.env`, `api.local.txt`, cache files, and personal notes
  are not staged.
- Keep `DRY_RUN=True` in `config.py`.
- Keep `LANGUAGE="en"` in `config.py`.
- Keep `HOST="127.0.0.1"` by default.
- Review `/api/save-env`; it must remain localhost-only or be removed before any
  hosted deployment.
- Run Python compile checks.
- Run a local secret scan.

## Commands

```powershell
python -m py_compile config.py server.py bot.py cex.py dex.py coingecko.py coin_identity.py
```

```powershell
git status --short
```

```powershell
rg -n --hidden --glob '!.git/**' --glob '!*.example*' "(API_KEY|SECRET|PASSPHRASE|PRIVATE_KEY|TOKEN|Bearer |sk-|AKIA|0x[a-fA-F0-9]{64})" .
```

## Do Not Commit

- `.env`
- `.env.local`
- `api.local.txt`
- `.coingecko_cache.json`
- `.coin_identity_cache.json`
- `.excluded_pairs.json`
- `__pycache__/`
- `openwolf/`
- personal notes or screenshots containing credentials
