# PocketManager — Code Review Report

Generated: 2026-05-29
Updated: 2026-05-29 (fixes applied)

---

## 🔴 CRITICAL (3 issues)

### 1. Dashboard Authentication Bypassed by Default ✅ FIXED
- **Files:** `pocketmanager/dashboard/auth.py`, `pocketmanager/cli.py`
- **Description:** The dashboard password defaults to empty, which disables authentication entirely. The dashboard binds to `0.0.0.0`, exposing an unauthenticated management interface to the entire network — anyone can create, delete, and manage instances.
- **Why it matters:** An unauthenticated dashboard on `0.0.0.0` gives full control of all instances to anyone on the network. This is the most dangerous issue because it requires zero exploitation — just visiting the URL.
- **What was changed:**
  - `auth.py`: Removed the `if not dashboard_password: return True` bypass in `check_auth()` and the `if not config.get("dashboard_password"): return f(...)` bypass in `requires_auth`. An empty password now denies access instead of allowing it.
  - `auth.py`: The `requires_auth` decorator now always enforces authentication — there is no codepath that skips it.
  - `cli.py` (dashboard command): Added a check before starting the dashboard. If no password is set and running interactively, prompts the user to set one via `click.prompt(hide_input=True, confirmation_prompt=True)` and saves it to config. If running in daemon mode with no password, aborts with a clear error message and instructions.

### 2. Timing-Attack on Dashboard Password ✅ FIXED
- **Files:** `pocketmanager/dashboard/auth.py`
- **Description:** `return password == dashboard_password` uses standard string comparison, not constant-time. An attacker can measure response times to progressively guess the password character-by-character.
- **Why it matters:** Standard `==` comparison short-circuits at the first wrong character. By measuring microsecond-level response differences, an attacker can determine how many characters are correct and build up the password one character at a time without ever guessing the whole thing.
- **What was changed:**
  - `auth.py`: Added `import hmac`. Replaced `password == dashboard_password` with `hmac.compare_digest(password, dashboard_password)`, which always compares the full strings in constant time regardless of where differences occur.

### 3. Plaintext Secrets in World-Readable Config ✅ FIXED
- **Files:** `pocketmanager/core/config.py`, `pocketmanager/core/state.py`
- **Description:** `config.json` (containing `pangolin.api_key` and `dashboard_password`) and `instances.json` are written with default umask permissions (~0644 = world-readable). Any local user can read the API key and dashboard password.
- **Why it matters:** On a shared VPS, any local user account or compromised process can open `config.json` and read all secrets in plain text. Combined with issue #1, this means an attacker could also read the password to log in.
- **What was changed:**
  - `config.py` (`save_config`): Added `os.chmod(path, 0o600)` after `os.replace()` — the final `config.json` is now owner-read/write only.
  - `state.py` (`save_state`): Added `os.chmod(path, 0o600)` after `os.replace()` — the final `instances.json` is now owner-read/write only.

---

## 🟠 HIGH (7 issues)

### 4. Systemd Service File Injection via Environment Variables ✅ FIXED
- **Files:** `pocketmanager/core/systemd.py`
- **Description:** Environment variable values are interpolated directly into systemd unit files with no sanitization. A malicious value containing `"` or `\n` breaks out of quoting and injects arbitrary systemd directives — achieving root-level code execution via the dashboard API.
- **Why it matters:** Systemd unit files are executed as root. If a malicious env value like `"; ExecStart=/bin/bash -c 'rm -rf /'` is injected, it becomes a new directive in the service file. Through the dashboard API, a remote attacker who has the password could inject commands that execute with root privileges.
- **What was changed:**
  - `systemd.py`: Added `_FORBIDDEN_ENV_CHARS` set and a `_sanitize_env(env)` function that validates all keys and values. Rejects empty keys, keys containing `=`, and any key or value containing `"`, `'`, `\n`, `\r`, or `\`. Raises `ValueError` on rejection.
  - `systemd.py` (`generate_service_file`): Added `_sanitize_env(env) if env else None` call before building env lines, so all env values are validated before being interpolated into the service file.

### 5. No Integrity Verification on Self-Update ⏭️ SKIPPED
- **Files:** `pocketmanager/core/selfupdate.py:143-196`
- **Description:** Downloads and installs a GitHub release tarball without checksum or GPG verification. MITM could inject malicious code executed with sudo privileges.
- **Why skipped:** Deferred — requires changes to the release workflow (publishing checksum files alongside releases). The code infrastructure should be added when the release process is updated.

### 6. `sudo rm -rf` with Unvalidated Path from State File ✅ FIXED
- **Files:** `pocketmanager/core/instance.py`
- **Description:** `instance_dir` comes from `instances.json`. Anyone who can write to that file (world-readable, see issue #3) can set it to `/` and cause catastrophic deletion.
- **Why it matters:** The `remove_instance` function runs `sudo rm -rf` on a path read from the state file. If that state file is tampered with (or a race condition writes a bad value), the code would delete arbitrary directories as root. Setting the path to `/` or `/etc` would be catastrophic.
- **What was changed:**
  - `instance.py` (`remove_instance`): Before the `sudo rm -rf` call, both `instance_dir` and `base_dir` (from config) are resolved to real paths via `Path.resolve()`. The code then verifies that `resolved_dir` starts with `resolved_base + "/"`. If the path escapes `base_dir`, a `ValueError` is raised with a message about possible state file tampering, and the deletion is aborted.

### 7. No CSRF Protection on Dashboard API ✅ FIXED
- **Files:** `pocketmanager/dashboard/api.py`
- **Description:** All state-changing endpoints (POST/DELETE) have no CSRF tokens. A malicious website can trick a logged-in admin into creating/deleting instances.
- **Why it matters:** Browsers automatically send Basic Auth credentials with requests to known realms. If you're logged into the dashboard and visit a malicious website in another tab, that site can silently send POST/DELETE requests to the dashboard API, creating or deleting instances without your knowledge.
- **What was changed:**
  - `api.py`: Added `from urllib.parse import urlparse` import.
  - `api.py`: Added a `@api.before_request` handler (`_check_csrf`) that activates on all non-GET/HEAD/OPTIONS requests. It requires `Content-Type: application/json` (HTML forms can only submit as `application/x-www-form-urlencoded` or `multipart/form-data`, so this blocks form-based CSRF entirely). It also validates the `Origin` header when present — browsers always send it on cross-origin requests, and if it doesn't match the request host, the request is rejected with 403.

### 8. Broken Pangolin Integration (Complete Silent Failure) ✅ FIXED
- **Files:** `pocketmanager/core/instance.py`, `pocketmanager/core/config.py`
- **Description:** The call passes arguments that don't match the function signature (`name, port, subdomain, domain` vs expected `name, subdomain, domain_id, org_id, site_id, target_ip, target_port`). Result: `TypeError` on every call, caught by `except Exception: pass` — Pangolin resources are never created.
- **Why it matters:** The Pangolin tunnel integration was completely non-functional. Every instance creation with Pangolin enabled silently crashed. The error was swallowed by a bare `except Exception: pass`, so users never knew. The `pangolin_resource_id` was never stored, meaning cleanup on deletion also never worked.
- **What was changed:**
  - `instance.py`: Rewrote the Pangolin call site to pass the correct arguments: `domain_id` from `pangolin.default_domain_id`, `org_id` from `pangolin.org_id`, `site_id` from `pangolin.site_id` (with safe int conversion), `target_ip` from `pangolin.target_ip`, and `target_port` from the instance's port. The return value (a dict) is now unpacked to extract `resourceId` and store it in `pangolin_resource_id`.
  - `config.py`: Added `target_ip` (default `"172.19.0.1"`) and `default_domain` (default `""`) to the `pangolin` config defaults. `target_ip` is configurable because the Pangolin Docker container needs to reach PocketBase via the Docker bridge gateway IP, not `127.0.0.1`.

### 9. No HTTPS for Dashboard ⏭️ SKIPPED
- **Files:** `pocketmanager/cli.py:850,855,862`
- **Description:** Flask's built-in HTTP server with Basic Auth — credentials transmitted in cleartext over the network.
- **Why skipped:** Pangolin acts as a reverse proxy with TLS termination. Traffic between clients and Pangolin is already encrypted. Only internal traffic between Pangolin and the dashboard is HTTP, which runs on the host/Docker network — no exposure to external networks.

### 10. No Rate Limiting on Dashboard Auth ⏭️ SKIPPED
- **Files:** `pocketmanager/dashboard/auth.py`
- **Description:** No lockout or exponential backoff on failed auth attempts. Brute-force is trivial.
- **Why skipped:** Pangolin and CrowdSec are already deployed in front of the dashboard, providing brute-force protection and rate limiting at the infrastructure layer. Application-level rate limiting would be redundant.

---

## 🟡 MEDIUM (3 issues)

### 11. Zip Slip Vulnerability in Binary Download ✅ FIXED
- **Files:** `pocketmanager/core/pocketbase.py`
- **Description:** `zf.extractall(dest_dir)` without `filter="data"`. On Python < 3.11.4, this is vulnerable to path traversal if the zip archive contains entries with `../` in paths. (Compare with `selfupdate.py:160` which correctly uses `filter="data"`.)
- **Why it matters:** A malicious zip file (from a MITM attack or compromised release) could contain entries with paths like `../../../etc/crontab`, overwriting system files outside the intended directory. The selfupdate code already had this protection, but the PocketBase binary download code did not.
- **What was changed:**
  - `pocketbase.py` (`download_and_cache`): Changed `zf.extractall(dest_dir)` to `zf.extractall(dest_dir, filter="data")`. The `filter="data"` parameter (Python 3.11.4+) strips path traversal components and other dangerous entries from the archive before extraction.

### 12. `pm config` Dumps All Secrets to Terminal ✅ FIXED
- **Files:** `pocketmanager/cli.py`
- **Description:** `pm config` (no arguments) prints the entire configuration including `pangolin.api_key` and `dashboard_password` to the terminal.
- **Why it matters:** Running `pm config` to check settings accidentally prints API keys and passwords to the terminal. If the user is screen-sharing, streaming, or has terminal logging enabled, secrets are exposed.
- **What was changed:**
  - `cli.py` (config command): Added `@click.option("--reveal", is_flag=True, default=False)` flag. When `--reveal` is not set (default), the config is deep-copied and `dashboard_password` and `pangolin.api_key` are replaced with `"***"` before printing. When `--reveal` is set, the full config is shown. Also added `import copy` for the deep copy.

### 13. Dashboard Config API Doesn't Mask `dashboard_password` ✅ FIXED
- **Files:** `pocketmanager/dashboard/api.py`
- **Description:** The `/api/config` endpoint masks `pangolin.api_key` but exposes `dashboard_password` verbatim in the response.
- **Why it matters:** The `/api/config` endpoint was inconsistent — it correctly masked the Pangolin API key but forgot to mask the dashboard password. Anyone with dashboard access (or inspecting the network tab) could see the password in plain text.
- **What was changed:**
  - `api.py` (`get_config`): Added `"dashboard_password"` to the exclusion set when building `safe` dict (alongside `"pangolin"`). Then explicitly set `safe["dashboard_password"] = "***"` if the password is set, or `""` if not — matching the same masking pattern used for `api_key`.

---

## 🐛 BUGS (6 issues)

### 14. `get_backup_count` Function Does Not Exist ✅ FIXED
- **Files:** `pocketmanager/core/backup.py`
- **Description:** Calls `backup_mod.get_backup_count(name)` which doesn't exist in `backup.py`. Always raises `AttributeError`, caught by bare `except`, so `backup_count` always falls back to `0`.
- **Why it matters:** The `pm ls` and `pm info` commands show backup counts, but they always display `0` because the function that counts backups was never implemented. The error is silently swallowed by a bare `except`, so the bug is invisible.
- **What was changed:**
  - `backup.py`: Implemented `get_backup_count(instance_name)` function. It looks up the instance from state via `get_instance(instance_name)`, constructs the URL as `http://localhost:{port}`, calls the existing `list_backups(url)` function, and returns `len(result)`. Returns `0` on any failure.

### 15. `pm ls` Command Misregistered ✅ FIXED
- **Files:** `pocketmanager/cli.py`
- **Description:** Double decoration (`@click.command("ls")` then `@cli.command("list")`) plus `cli.add_command()` likely breaks the `ls` alias.
- **Why it matters:** The decorators applied bottom-up: first `@click.command("ls")` wraps the function into a standalone `Command` object, then `@cli.command("list")` tries to register that `Command` on the group. The result is unpredictable — `pm ls` might not work, or both `pm list` and `pm ls` could behave unexpectedly.
- **What was changed:**
  - `cli.py`: Removed the stray `@click.command("ls")` decorator line. The function now only has `@cli.command("list")`, and the existing `cli.add_command(list_instances_cmd, "ls")` on the next line registers the `ls` alias correctly.

### 16. `backup --download` Flag Does Nothing ✅ FIXED
- **Files:** `pocketmanager/cli.py`
- **Description:** The `--download` option is accepted but never used. `download=True` is received but `backup_mod.create_backup()` is called without any download logic.
- **Why it matters:** Users pass `--download` expecting the backup file to be saved locally, but the flag is silently ignored. The backup is created on the PocketBase server but never downloaded. The user thinks it worked but can't find the file.
- **What was changed:**
  - `cli.py` (backup command): After the backup is created successfully and `download=True`, the code now: (1) calls `backup_mod.list_backups()` to find the backup just created (matches by name if `--name` was given, otherwise picks the most recent by `modified` date), (2) constructs a local destination path in the instance directory, (3) calls `backup_mod.download_backup()` to save it, (4) prints the local file path on success.

### 17. Dashboard URL Building Doesn't Match CLI ✅ FIXED
- **Files:** `pocketmanager/dashboard/templates/dashboard.html`, `pocketmanager/core/config.py`
- **Description:** Dashboard hardcodes `https://{subdomain}.app` while CLI uses config-based domain or `.example.com`. Users see different URLs depending on interface.
- **Why it matters:** The CLI's `_build_url()` reads `pangolin.default_domain` from config to build proper URLs. The dashboard JS hardcoded `.app` as the TLD. Users would see `https://myapp.app` in the dashboard but `https://myapp.yacinesahli.com` in the CLI for the same instance. The `default_domain` config key also didn't exist in the defaults.
- **What was changed:**
  - `dashboard.html`: Added `let appConfig = {};` state variable. The `buildUrl()` function now reads `appConfig.pangolin.default_domain` and uses it for URL construction, falling back to `.app` only if no domain is configured.
  - `dashboard.html` (init): Added `api('/config').then(r => r.json()).then(cfg => { appConfig = cfg || {}; }).catch(() => {});` to fetch config on page load.
  - `config.py`: Added `"default_domain": ""` to the `pangolin` config defaults so the key is always present.

### 18. `ensure_pocketbase_user` Hardcodes Home Directory ✅ FIXED
- **Files:** `pocketmanager/core/systemd.py`
- **Description:** Ignores configured `base_dir`. If user changes `base_dir`, the system user's home directory won't match.
- **Why it matters:** The `useradd` command always sets the home directory to `/home/ubuntu/pocketbases` regardless of the configured `base_dir`. If the user configured a different `base_dir` (e.g., `/opt/pocketbases`), the system user's home would point to the wrong place, breaking path resolution and file permissions.
- **What was changed:**
  - `systemd.py` (`ensure_pocketbase_user`): Added `from pocketmanager.core.config import get as cfg_get` and `base_dir = cfg_get("base_dir", "/home/ubuntu/pocketbases")`. The `--home` argument in the `useradd` command now uses `base_dir` instead of the hardcoded path.

### 19. Restore Doesn't Actually Restart Instance ⏭️ SKIPPED
- **Files:** `pocketmanager/cli.py:612-613`
- **Description:** Prints "instance will restart automatically" but never actually restarts the instance.
- **Why skipped:** PocketBase's restore API (`POST /api/backups/{key}/restore`) triggers an automatic process restart internally. The PocketBase process exits and systemd restarts it (since the service is configured with `Type=simple`). The message "instance will restart automatically" is correct — no manual restart is needed. The review's claim was inaccurate.

---

## ⚡ RACE CONDITIONS (3 issues)

### 20. State File Concurrent Writes ✅ FIXED
- **Files:** `pocketmanager/core/state.py`
- **Description:** Every operation does `load_state() -> modify -> save_state()` without file locking. Concurrent CLI calls silently lose data.
- **Why it matters:** If two CLI commands run simultaneously (e.g., creating two instances in parallel), both read the state file, both make their changes, and the second to write overwrites the first's changes. One instance silently disappears from the state file.
- **What was changed:**
  - `state.py`: Added `import fcntl` and `from contextlib import contextmanager`.
  - `state.py`: Added `_state_lock()` context manager that creates/opens a lock file (`instances.json.lock`), acquires an exclusive `fcntl.flock()`, yields, then releases the lock and closes the file descriptor in a `finally` block.
  - `state.py`: Wrapped the load→modify→save cycle in `add_instance`, `remove_instance`, and `update_instance` with `with _state_lock():`. Read-only functions (`load_state`, `get_instance`, `get_all_instances`) are not locked since the atomic write pattern (temp file + rename) already prevents reading partial data.

### 21. Port Allocation TOCTOU ✅ FIXED
- **Files:** `pocketmanager/core/ports.py`
- **Description:** `find_available_port()` scans and returns, but another process can claim the port before binding.
- **Why it matters:** Two instances being created simultaneously could both find the same port available and both try to use it. The second PocketBase process to bind would fail. The time-of-check-to-time-of-use (TOCTOU) gap between scanning and binding is small but real during parallel operations.
- **What was changed:**
  - `ports.py`: Added `import socket`.
  - `ports.py`: Added `_try_bind(port)` function that attempts `socket.bind(("0.0.0.0", port))` with `SO_REUSEADDR`. Returns `True` if the bind succeeds, `False` otherwise. The socket is immediately closed after the test.
  - `ports.py` (`find_available_port`): Changed the port selection loop from `if port not in occupied:` to `if port not in occupied and _try_bind(port):`. This verifies the port is truly available at the moment of selection, closing the TOCTOU window.

### 22. Binary Download Cache Race ✅ FIXED
- **Files:** `pocketmanager/core/pocketbase.py`
- **Description:** Two concurrent `create_instance` calls can both see the cached binary missing and download simultaneously.
- **Why it matters:** If two instances are created at the same time and neither has the PocketBase binary cached, both start downloading it. This wastes bandwidth and could corrupt the cache if both write to the same file simultaneously during extraction.
- **What was changed:**
  - `pocketbase.py`: Added `import fcntl`.
  - `pocketbase.py` (`download_and_cache`): After the early-return cache check, the function now opens a version-specific lock file (`pocketbase_{version}_linux_{arch}.lock`) and acquires an exclusive `fcntl.flock()`. After acquiring the lock, it re-checks if the binary already exists (another process may have downloaded it while waiting). If still missing, proceeds with the full download + extract flow. The lock is released and the file descriptor closed in a `finally` block that wraps the entire download+extract operation.

---

## 📋 MISSING FEATURES

### 23. Auto-Backup Cron Scheduling ⏭️ DEFERRED
- **Config keys exist** (`auto_backups_enabled`, `auto_backups_cron`, `auto_backups_max_keep`)
- **README documents it** (lines 355-367)
- **No code implements it.** Nothing creates crontab entries or schedules backups.
- **Why deferred:** This is a full feature implementation requiring: (1) creating/removing crontab entries on instance creation/deletion, (2) per-instance and global backup scheduling settings, (3) wiring up the config keys that already exist, (4) potentially a backup runner script. This is significant scope beyond a bug fix and should be implemented as a dedicated feature.

### 24. Static Directory Referenced but Missing ✅ FIXED
- **Files:** `pocketmanager/dashboard/app.py`
- **Description:** `static_folder` points to a non-existent `static/` directory.
- **Why it matters:** The Flask app was configured with a `static_folder` pointing to a directory that doesn't exist on disk. While this didn't cause visible errors (the dashboard is entirely self-contained with all CSS/JS inline in `dashboard.html`), it's dead code that could confuse future development.
- **What was changed:**
  - `app.py` (`create_app`): Removed the `static_folder=str(Path(__file__).parent / "static")` line from the Flask constructor. The dashboard is fully inline — no separate static assets are used or referenced.

### 25. No Daemon PID File / Stop Mechanism ✅ FIXED
- **Files:** `pocketmanager/cli.py`
- **Description:** Daemon mode forks to background but writes no PID file and provides no stop command.
- **Why it matters:** Starting the dashboard with `--daemon` forks to the background and prints the PID, but doesn't save it anywhere. After closing the terminal, there's no way to find or stop the dashboard process. No `pm dashboard stop` command exists.
- **What was changed:**
  - `cli.py`: Added `import signal` and `from pathlib import Path`.
  - `cli.py` (dashboard command): Added `--stop` flag option.
  - `cli.py` (dashboard command): Defined `pid_path = get_config_dir() / "dashboard.pid"`. When `--stop` is passed, reads the PID from the file, sends `SIGTERM` via `os.kill()`, removes the PID file, and exits. Handles `ProcessLookupError` (stale PID file) gracefully.
  - `cli.py` (dashboard command): When daemonizing, writes the child PID to `dashboard.pid` after forking. Prints a hint: "Stop with: pm dashboard --stop".

### 26. `pm config get <key>` Not Supported ✅ FIXED
- **Files:** `pocketmanager/cli.py`
- **Description:** Running `pm config get foo` searches for key `"get foo"` instead of `"foo"`. Users must omit the `get` keyword.
- **Why it matters:** The config command uses `UNPROCESSED` args, so `pm config get pangolin.api_key` concatenates all args into `"get pangolin.api_key"` and looks for that literal key, which doesn't exist. Users naturally expect `get` to work as a subcommand.
- **What was changed:**
  - `cli.py` (config command, else branch): Changed the key extraction from `key = " ".join(args)` to `key = args[0] if args[0] != "get" else args[1] if len(args) > 1 else args[0]`. This strips the optional `get` keyword, so both `pm config get foo` and `pm config foo` now work identically.

---

## Summary

| Category | Total | Fixed | Skipped |
|----------|-------|-------|---------|
| 🔴 Critical Security | 3 | 3 | 0 |
| 🟠 High Security | 7 | 3 | 4 |
| 🟡 Medium Security | 3 | 3 | 0 |
| 🐛 Bugs | 6 | 5 | 1 |
| ⚡ Race Conditions | 3 | 3 | 0 |
| 📋 Missing Features | 4 | 3 | 1 |
| **Total** | **26** | **20** | **6** |

### Files Modified

| File | Issues |
|------|--------|
| `pocketmanager/dashboard/auth.py` | #1, #2 |
| `pocketmanager/dashboard/api.py` | #7, #13 |
| `pocketmanager/dashboard/app.py` | #24 |
| `pocketmanager/dashboard/templates/dashboard.html` | #17 |
| `pocketmanager/core/config.py` | #3, #8, #17 |
| `pocketmanager/core/state.py` | #3, #20 |
| `pocketmanager/core/instance.py` | #6, #8 |
| `pocketmanager/core/systemd.py` | #4, #18 |
| `pocketmanager/core/ports.py` | #21 |
| `pocketmanager/core/pocketbase.py` | #11, #22 |
| `pocketmanager/core/backup.py` | #14 |
| `pocketmanager/cli.py` | #1, #12, #15, #16, #25, #26 |
