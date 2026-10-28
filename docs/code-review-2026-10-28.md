# PocketManager — Deep Review (2026-10-28)

Reviewed at commit `ea16cc4` (master). Scope: all 13 core modules, CLI, dashboard (incl. full 1475-line template), install.sh, tests, packaging. Tests executed: **55 passed / 0 failed / 8 setup errors** (errors are Linux-only fixtures downloading a Linux ELF binary — environmental on macOS, not code defects). No lint tooling or CI exists.

> **FIX PASS 2026-10-28 (same day, uncommitted):** C1, C2, H1–H6, M3, M7, M9, M10 (setsid), M12, M13 (unchanged — content-type gate retained), M15, M17 (partial), plus LOWs (f-string, KeyError→400, compare_digest encoding, lines cap, dead imports) are fixed. New: dashboard tests (13) + GitHub Actions CI. After fixes: **68 passed / 0 failed / same 8 environmental errors**. Remaining open: M4 (ExecStartPost sleep race — path fixed, semantics unchanged), M5 (self-update pinning/timeout — only the None→"unknown" reporting fixed), M6 (legacy crontab cleanup added to remove; root-user fallback added), M8 (binary checksums), M11 (state locking/schema), H3c (superadmin password in argv — needs PocketBase env-var support), H2 residual (TOFU instead of pre-pinned keys), plus the LOW items not listed above. Commit `commit.txt` refresh deferred to commit time.

---

## Verdict

Solid feature breadth and clean subprocess hygiene (zero `shell=True`, argv-lists everywhere, no command injection found), but the tool is **not production-safe yet**: two critical correctness bugs, an internet-exposed dashboard resting on one unthrottled password, plaintext secrets in two files, and several recently-shipped features (backup cron status, migration auto-backup, cron→timer conversion) that silently don't work.

---

## CRITICAL

### C1. Instances are never `systemctl enable`d — total outage after VPS reboot
`create_service` (systemd.py:166-215) writes the unit and runs `daemon-reload` but never enables it; `start_service` (systemd.py:388-402) only starts. Unit declares `WantedBy=multi-user.target` (systemd.py:155) but the symlink is never created. `remove_service` calls `disable` (systemd.py:353), proving enable was intended. **Every instance dies permanently on reboot while `pm create` reports success.** Fix: `systemctl enable <unit>` after daemon-reload in `create_service` (and on migration).

### C2. Backup-cron status can never work — no auth, so `pm ls` misreports backup state
`get_backup_cron_status(instance_url)` (backup.py:122-146) calls `GET /api/settings` — superuser-only in PocketBase v0.23+ — with **no auth token parameter at all**. Always 401 → `{"enabled": False}`. Callers (`pm ls` cli.py:300, `pm local-backup-schedule` cli.py:1346) silently fall back to the PM-side `auto_backup` flag, which defaults to `True` (instance.py:313) even when nothing is configured. The feature from commit 330a006 is dead on arrival and actively misleading. Related: migration's "configure cron on migration" (instance.py:698-703) calls `configure_auto_backup` with no token → 401 → swallowed by bare `except: pass`.

---

## HIGH

### H1. Network exposure model is void: 0.0.0.0 everywhere + no firewall anywhere
- Dashboard: `app.run(host="0.0.0.0", port=8888)` (app.py:26, cli.py:2003/2010), Basic auth in cleartext, no TLS, no rate limiting/lockout (auth.py:28-39), no minimum password policy — 1-char password accepted (cli.py:1967-1971).
- Every PocketBase instance binds `--http=0.0.0.0:{port}` (systemd.py:134), exposing each PB admin UI `/_/` on ports 8090–8999.
- install.sh and README contain **zero firewall configuration** (no ufw/iptables match). On a default-open VPS, anyone reaching the IP skips Pangolin entirely.
- Same dashboard password is reused as the Pangolin resource password (cli.py:1905-1907) — one leak = both layers.

### H2. SFTP host-key policy is `AutoAddPolicy` (sftp.py:102)
Every backup upload accepts any host key — a MITM captures the SFTP password and full database backups. No known_hosts persistence.

### H3. Secret-handling gaps
- `pm config` prints `sftp.password` in cleartext — masking covers only `dashboard_password` and `pangolin.api_key` (cli.py:1725-1745, verified). Same omission in dashboard `/api/config` (api.py:208-213).
- Passwords on CLI flags (`--password` for SFTP cli.py:1084, Pangolin auth cli.py:2149) → shell history + `ps`.
- Superadmin passwords stored plaintext in `instances.json` (instance.py:314-315, state.py:154, mode 0660) — group-readable by installer's group.
- Superadmin password visible in `/proc/*/cmdline` during `superuser upsert` (instance.py:131-136).

### H4. `pm backup --download` cannot work by default
Downloads to `{instance_dir}/{key}` (cli.py:806-816) but the instance dir is chowned `pocketbase:pocketbase` (systemd.py:632-641) → `PermissionError`, swallowed by blanket except (backup.py:234-235) → generic "Failed to download backup."

### H5. Update flow: no rollback, and sudo failures surface as raw tracebacks
`update_instance` (instance.py:543-573) stops service, swaps binary; failed start leaves instance stopped with `pocketbase.bak` and no rollback. All sudo `CalledProcessError`s escape the CLI's `(ValueError, RuntimeError)` handlers (cli.py:1622-1626, 201-203) — the most common failure mode (sudo expired) dumps a Python traceback.

### H6. Cron→OnCalendar converter silently mis-schedules (cron.py:64-80)
`*/15 * * * *` becomes **daily at midnight** (non-digit fields default to 0). DOW `1-5` passes through unmapped → systemd rejects → `set_sftp_cron` returns False with zero diagnostics (exception swallowed cron.py:338-339). Month field ignored entirely.

---

## MEDIUM

| # | Finding | Where |
|---|---------|-------|
| M1 | Read-only `pm ls` triggers sudo (password prompt on listing) | cron.py:125-132, 268-287 via cli.py:278 |
| M2 | `create_instance` ignores start failure; TOCTOU port allocation; registered even if service dead | instance.py:259, 317; ports.py:96-131 |
| M3 | `migrate_existing`: no name validation (`^[a-z0-9-]+$` never applied), can silently re-port a live instance, can append `None` to state | instance.py:604, 647-652, 719 |
| M4 | ExecStartPost hardcodes `/usr/bin/curl` but gates on `which("curl")` + `/bin/sleep 2` race; `StartLimitBurst=5` can permanently kill slow starters | systemd.py:107-114, 128-131 |
| M5 | Self-update: floating `@master` + `--force-reinstall`, no pinning/hash; 120s timeout can abort mid-install; marker written to possibly-shadowed old package tree; API failure reported as "up to date" | selfupdate.py:18, 141, 151-153, 96-99 |
| M6 | Timer service user = owner of `pm` binary → root if system-wide install (contradicts docstring); legacy root crontab only cleaned on re-enable, never on remove → double backups possible | cron.py:111-122, 197-232, 342-352 |
| M7 | SFTP temp file `/tmp/pm_sftp_{inst}_{key}`: predictable, world-readable umask, symlink-following, `key` with `/` escapes temp dir | sftp.py:297-299, 317-321; cli.py:1228 |
| M8 | PocketBase binary downloaded with no checksum verification (checksums.txt exists upstream); cached binary trusted forever + gets `cap_net_bind_service` | pocketbase.py:157-204, 210-214 |
| M9 | `remove_instance` guard: `instance_dir == base_dir` passes the check → state corruption could `sudo rm -rf` every instance | instance.py:379-381 |
| M10 | Dashboard daemon = forked Flask dev server on 0.0.0.0, no setsid (dies with terminal), no systemd unit | cli.py:1988-2010 |
| M11 | State reads lock-free; instance record shapes divergent (create vs migrate); root-created state dir gets chowned to root by `_fix_file_ownership` | state.py:119-131, 40-52; instance.py:305-316 vs 706-717 |
| M12 | Stored XSS: `onclick="'${escAttr(name)}'"` decodes entities before JS exec (dashboard.html:965, 1002-1048); `escHtml` doesn't escape quotes but is used in attributes (:964). Reachable via unvalidated `migrate_existing` names (M3) — the create path's regex is the only thing preventing it | instance.py:604 + dashboard.html |
| M13 | CSRF = content-type check only (api.py:18-34); `sendBeacon` with JSON Blob historically bypasses in some browsers; all POST endpoints depend on it | api.py |
| M14 | install.sh Pangolin error handler dead under `set -e` — failure aborts before the `if [[ $? -eq 0 ]]` guard; API key echoed to terminal (no `read -s`) | install.sh:300-309, 271 |
| M15 | `requirements.txt` missing `paramiko` (pyproject has it) → ImportError on `pip install -r requirements.txt` path | requirements.txt vs pyproject.toml:34 |
| M16 | `commit.txt` (self-update fingerprint) manually maintained, currently 4 commits stale → every fresh install sees phantom "update available" and reinstalls identical code once. No automation (no scm plugin, no CI write-back) | commit.txt:1 vs HEAD |
| M17 | `pm config get` with no key collapses to key `"get"` → confusing error; `logs` endpoint `lines` param uncapped (journalctl can stream entire journal) | cli.py:1774; api.py:144 |

---

## LOW (selection)

- cli.py:524-526 — warning string not an f-string, literally prints `{name}`.
- cli.py:1118 — help example `pm sftp-config --password` doesn't match actual option signature (requires argument).
- Dead code: sftp.py:36-38 unused imports, :139-140 unused var; systemd.py:102 expression-statement no-op; instance.py:265-268 double import (`noqa: F811`).
- Auth header inconsistency: `Authorization: <token>` vs `Bearer <token>` mixed in backup.py (106/163/253/279 vs 187).
- `backup_key` not URL-encoded (backup.py:224, 255, 281); latest-backup selection copy-pasted 4×, `startswith` match can pick wrong backup (cli.py:789-799 etc.).
- `hmac.compare_digest` on str raises TypeError on non-ASCII Basic credentials (auth.py:18) → HTTP 500.
- `data["name"]` KeyError → 500 instead of 400 (api.py:69).
- No security headers (CSP/XFO) on dashboard HTML.
- Uptime parsing assumes local time (DST off-by-one) (systemd.py:510-523).
- Unauthenticated GitHub API (60 req/hr) can fail version checks on busy boxes.
- Version `0.1.0` triplicated (pyproject, `__init__`, install.sh banner) and drifting from tag `v0.1.0` (4+ commits past it).

---

## Architecture / maintainability

1. **cli.py is a god-file** (2215 lines, ~30 commands): mixes parsing, SFTP config UI, dashboard bootstrap + Pangolin setup, table rendering, and business logic. A `cli/` package split by domain (instances / backup / sftp / dashboard / config) would cut it by half+. The 30+ function-body imports exist to dodge import cost/cycles — a symptom, not a design.
2. **Duplication**: latest-backup pick ×4, SFTP prune block ×2, `_require_sftp_config` re-declares config defaults (cli.py:938-947 vs config.py:61-70), cron.py re-implements its own sudo/unit-file helpers instead of reusing systemd.py.
3. **Inconsistent error contracts**: some raise, some return bool, some return bool that callers ignore (`ensure_pocketbase_user` instance.py:212, `start_service` instance.py:259); `CalledProcessError` escapes the CLI boundary everywhere (H5).
4. **Conventions drift**: `localhost` vs `127.0.0.1`, `superadmin` vs PB's `superuser`, case-insensitive state lookups vs lowercase-only name regex.

## Test-suite gaps

- **Dashboard: zero tests** — auth, CSRF gate, secret masking (would have caught M3/MEDIUM SFTP leak), XSS rendering all untested.
- Untested modules: `pangolin.py`, `sftp.py`, `cron.py`, `selfupdate.py`, `pocketbase.py` download/cache, `migrate_existing`, `update`/`update-all`/`sftp-*`/`backup-all`/`credentials` commands.
- Weak assertions: `test_config_show` accepts exit 0 or 1 (test_cli.py:157-158); `test_config_reveal` asserts only `if exit_code == 0` (:207-215).
- Dead fixture `mock_systemd` (conftest.py:278-332) used by zero tests.
- No CI config, no lint tooling, no security-negative tests.

---

## Recommended fix order (one pass, highest leverage first)

1. `systemctl enable` on create/migrate (C1) — two-line fix, catastrophic failure mode.
2. Thread auth tokens through backup status/config + migration (C2) — feature is currently fiction.
3. Bind dashboard + PB instances to `127.0.0.1` (Pangolin targets localhost fine) and/or add a UFW step to install.sh (H1).
4. Mask `sftp.password` in `pm config` + `/api/config`; move password input to prompts (`hide_input=True`) (H3).
5. Fix or reject unsupported cron expressions in the converter (H6).
6. Catch `CalledProcessError` at the CLI boundary + auto-rollback on failed update start (H5).
7. Replace `AutoAddPolicy` with known_hosts persistence (H2).
8. Validate migrated names with the existing regex + fix the `onclick` escaping pattern (M3/M12).
9. Add `paramiko` to requirements.txt, automate `commit.txt`/version via setuptools_scm or CI write-back (M15/M16).
10. Add dashboard tests (auth, masking, CSRF gate) + GitHub Actions CI running the Linux suite.
