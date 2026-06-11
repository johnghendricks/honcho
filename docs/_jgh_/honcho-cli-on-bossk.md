# honcho CLI on Bossk → Mando — Setup & Runbook

> Personal runbook for John (jgh). Install and use the **`honcho` CLI**
> (`honcho-cli`, in-repo at `honcho-cli/`) from a terminal on **Bossk**
> (Windows 11 Pro workstation), pointed at the self-hosted Honcho server on
> **Mando** (MacBook Pro M1 Max). Set up 2026-06-11.

## TL;DR

```powershell
honcho workspace list                 # sanity check — should return: default
honcho workspace inspect -w default   # peers, sessions, config
honcho peer list -w default
honcho peer chat "what do you know about X" -w default -p <peer>
honcho workspace queue-status -w default
```

The CLI is a **client**, not a server tool — a Typer app that talks to a Honcho
server over HTTP via the Python SDK. It runs on Bossk and points at Mando's API
(`http://192.168.0.225:8000`). Nothing needs to be installed on Mando for this.

## Connection facts

| Field | Value |
| --- | --- |
| Executable | `C:\Users\John Hendricks\.local\bin\honcho.exe` (on PATH) |
| Config file | `C:\Users\John Hendricks\.honcho\config.json` |
| `environmentUrl` | `http://192.168.0.225:8000` (Mando Honcho API) |
| `apiKey` | none — Mando runs `AUTH_USE_AUTH=false` (trusted LAN) |
| Source | `honcho-cli/` in this repo (installed editable) |

### `~/.honcho/config.json`

```json
{
  "environmentUrl": "http://192.168.0.225:8000"
}
```

The CLI owns exactly two top-level keys in this file (`apiKey`, `environmentUrl`)
and preserves any others that sibling Honcho tools write. Workspace / peer /
session scoping is **never** persisted here — pass `-w` / `-p` / `-s` per command
or set `HONCHO_WORKSPACE_ID` / `HONCHO_PEER_ID` / `HONCHO_SESSION_ID` per shell.

## How it was installed (replicate)

Installed editable from the in-repo source so local `honcho-cli/src/` changes
track live without reinstalling:

```powershell
# from the honcho repo root (D:\Git\Open-Source\honcho)
uv tool install --force --editable --from ./honcho-cli --with click honcho-cli
```

> ⚠️ **`--with click` is required.** The editable build of typer 0.26.7 does not
> pull in `click`, so a plain install fails at runtime with
> `ModuleNotFoundError: No module named 'click'`. This is a genuine packaging gap
> in `honcho-cli/pyproject.toml` (it should declare `click` directly); `--with click`
> injects it into the tool environment as a workaround.

Then write the config (auth off → no `init` prompt needed):

```powershell
New-Item -ItemType Directory -Force "$HOME\.honcho" | Out-Null
'{ "environmentUrl": "http://192.168.0.225:8000" }' | Set-Content "$HOME\.honcho\config.json"
```

Verify with a real command (not `doctor` — see gotcha below):

```powershell
honcho workspace list      # → [ { "id": "default" } ]
```

## Gotchas

### `honcho doctor` shows a false negative with auth off

`doctor` gates its connectivity probe on an API key being present
(`honcho-cli/src/honcho_cli/commands/setup.py:238` — `if config.base_url and config.api_key`).
With auth off there's no key, so it reports:

```
API key configured   missing — run `honcho init`
API connectivity     skipped — no base_url or api_key
```

**Ignore it.** Real commands send an API key only when one is configured, and
work fine against the auth-off server. Use `honcho workspace list` as the true
connectivity check instead of `doctor`.

### `click` missing

See the install note above — always include `--with click`.

## Scoping & output

```powershell
# Per-command flags (flag > env var > config file > default)
honcho peer card -w default -p <peer>

# Or export once per shell
$env:HONCHO_WORKSPACE_ID = "default"
honcho peer list

# One-off against a different server
$env:HONCHO_BASE_URL = "http://localhost:8000"; honcho workspace list
```

Output is rich-formatted in an interactive terminal and auto-switches to JSON
when stdout isn't a TTY (piped/redirected). Force JSON with `--json` or
`HONCHO_JSON=1`. Errors are structured (`{ "error": { "code", "message", "details" } }`).

## Common commands

```powershell
# Workspaces
honcho workspace list
honcho workspace inspect -w default
honcho workspace search "<query>" -w default
honcho workspace queue-status -w default        # deriver queue health

# Peers
honcho peer list -w default
honcho peer inspect <id> -w default             # card, session count, recent conclusions
honcho peer card <id> -w default
honcho peer chat "<query>" -w default -p <peer> # query the dialectic
honcho peer representation <id> -w default

# Sessions
honcho session list -w default
honcho session inspect <id> -w default
honcho session context <id> -w default          # what an agent would see
honcho session summaries <id> -w default

# Conclusions (observations)
honcho conclusion list -w default --observer <p> --observed <p>
honcho conclusion search "<query>" -w default

# Config (api key redacted)
honcho config
```

## Updating the CLI

Because it's installed editable, edits to `honcho-cli/src/` are picked up live —
no reinstall needed. Re-run the install command only to refresh dependencies or
after pulling changes that touch `pyproject.toml`:

```powershell
uv tool install --force --editable --from ./honcho-cli --with click honcho-cli
```

## Related

- `docs/_jgh_/ssh-mando-from-bossk.md` — SSH into Mando (driving the server side).
- `docs/_jgh_/honcho-mando-ollama-setup.md` — the Mando Honcho + Ollama deployment.
- Mando facts: `192.168.0.225`, Honcho API `:8000`, MCP `:8787`, Ollama `:11434`.
