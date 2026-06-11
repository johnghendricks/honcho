# SSH: Bossk → Mando — Setup & Runbook

> Personal runbook for John (jgh). Passwordless key-based SSH from **Bossk**
> (Windows 11 Pro workstation) to **Mando** (MacBook Pro M1 Max, the Honcho +
> Ollama server) so "things Mando" can be driven from a terminal on Bossk.
> Set up 2026-06-11.

## TL;DR

```powershell
ssh mando                       # interactive shell on Mando
ssh mando "docker ps"           # Honcho stack containers
ssh mando "ollama ps"           # loaded models on the Metal GPU
```

No password, no passphrase. Works from any PowerShell/terminal on Bossk.
There's also a `mando` PowerShell function (see below) so you can drop the `ssh`
and quotes: `mando docker ps`.

## Connection facts

| Field | Value |
| --- | --- |
| Host alias | `mando` (defined in Bossk `~/.ssh/config`) |
| Address | `192.168.0.225` (Mando, LAN) |
| Mac account | `johnhendricks` |
| Login shell | `/bin/zsh` |
| Auth | ed25519 public key, **no passphrase** |
| Private key (Bossk) | `~/.ssh/id_ed25519` |
| Authorized key (Mando) | `~/.ssh/authorized_keys` |

### Bossk `~/.ssh/config`

```
Host mando
    HostName 192.168.0.225
    User johnhendricks
    IdentityFile ~/.ssh/id_ed25519
    ServerAliveInterval 60
```

## How it was set up (replicate on a new client)

1. **Generate a keypair on the client** (ed25519, no passphrase):
   ```powershell
   ssh-keygen -t ed25519 -f "$env:USERPROFILE\.ssh\id_ed25519" -N '' -C "you@client"
   ```
   > ⚠️ **PowerShell quoting gotcha.** Use `-N ''` (empty), **not** `-N '""'`.
   > Single quotes make `'""'` the *literal two characters* `""`, which becomes
   > a real passphrase. Symptom: the handshake logs `Server accepts key` (public
   > key matched) then `Permission denied` (private key couldn't be decrypted
   > under `BatchMode`), and interactive `ssh` prompts `Enter passphrase for key`.
   > Fix without re-copying the key: `ssh-keygen -p -f <key> -P '""' -N ''`.

2. **Enable Remote Login on Mando** (one-time, done on the Mac itself):
   - GUI: System Settings → General → Sharing → **Remote Login** on (ensure
     `johnhendricks` is allowed).
   - or CLI on Mando: `sudo systemsetup -setremotelogin on`
   - Verify reachable from Bossk: `Test-NetConnection 192.168.0.225 -Port 22`

3. **Install the public key on Mando** (prompts for Mac password once; run it
   yourself so the prompt lands in your interactive session):
   ```powershell
   type "$env:USERPROFILE\.ssh\id_ed25519.pub" | ssh johnhendricks@192.168.0.225 `
     "umask 077; mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys"
   ```

4. **Write the `~/.ssh/config` alias** on Bossk (see block above).

5. **Verify passwordless:**
   ```powershell
   ssh -o BatchMode=yes mando "echo OK; hostname; whoami"
   ```

## PATH fix — why `docker`/`ollama` resolve over SSH

Non-interactive SSH on macOS gets a **stripped `PATH`** (`/usr/bin:/bin:/usr/sbin:/sbin`),
which omits `/usr/local/bin` where `docker` and `ollama` live. Without the fix,
`ssh mando "docker ps"` fails with command-not-found.

Because the login shell is **zsh**, and zsh sources `~/.zshenv` on *every*
invocation (including non-interactive `ssh host "cmd"`), the fix is a one-line
`~/.zshenv` on Mando:

```sh
# ~/.zshenv on Mando
# Added for non-interactive SSH (docker, ollama, brew tools on PATH)
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
```

This is why plain `ssh mando "docker ps"` works without a `bash -lc '...'` wrapper.

> If you ever switch the remote default shell away from zsh, the equivalent file
> changes: bash login shells read `~/.bash_profile`/`~/.bashrc`, but only zsh's
> `.zshenv` is guaranteed for *non-interactive* command runs — hence zsh + `.zshenv`.

## Common "things Mando" commands

```powershell
# Honcho stack (containers: honcho-api-1, honcho-database-1, honcho-redis-1)
ssh mando "docker ps"
ssh mando "cd ~/honcho && docker compose logs -f --tail=50"
ssh mando "cd ~/honcho && docker compose restart deriver"

# Ollama (native, Metal GPU)
ssh mando "ollama ps"
ssh mando "ollama list"

# Quoting tip: keep inner quotes single, outer double — passes through cleanly:
ssh mando "docker ps --format '{{.Names}}\t{{.Status}}'"
```

## `mando` PowerShell helper (Bossk)

A convenience wrapper so you can type `mando docker ps` instead of
`ssh mando "docker ps"`. Installed in **both** PowerShell profiles on Bossk:

- `~\Documents\WindowsPowerShell\Microsoft.PowerShell_profile.ps1` (Windows PowerShell 5.1)
- `~\Documents\PowerShell\Microsoft.PowerShell_profile.ps1` (PowerShell 7 / pwsh)

```powershell
# Bossk -> Mando SSH helper (see honcho repo _jgh_/docs/ssh-mando-from-bossk.md).
#   mando                                   # interactive shell on Mando
#   mando docker ps                         # run a command on Mando
#   mando "docker ps --format '{{.Names}}'" # quote complex commands as one arg
function mando {
    if ($args.Count -eq 0) {
        ssh mando
    } else {
        ssh mando ($args -join ' ')
    }
}
```

Usage:

```powershell
mando                                    # interactive shell on Mando
mando ollama ps
mando docker ps
mando "docker ps --format '{{.Names}}'"  # single-arg form for spaces/tabs in the format
```

> The function joins `$args` with spaces, so unquoted multi-word flags lose their
> grouping. For anything with embedded spaces (e.g. a `--format` with `\t`), pass
> the whole command as one quoted string. New terminals pick it up automatically;
> reload an open one with `. $PROFILE`.

## Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| `Permission denied (publickey...)`, but logs show `Server accepts key` | Private key has a passphrase (the `-N '""'` bug). Strip it: `ssh-keygen -p -f ~/.ssh/id_ed25519 -P '""' -N ''`. |
| `Permission denied` with no `Server accepts key` | Key not in Mando `~/.ssh/authorized_keys`, or StrictModes perms: `~` not group/world-writable, `~/.ssh` = 700, `authorized_keys` = 600. |
| `TcpTestSucceeded: False` | Remote Login off on Mando — re-enable (step 2). |
| `docker: command not found` over SSH | `~/.zshenv` PATH fix missing (see above). |
| Host key changed warning | Mando reinstalled/changed; remove stale line from Bossk `~/.ssh/known_hosts`. |
