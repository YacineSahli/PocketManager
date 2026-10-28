# PocketManager — Product / UX / Maintainability Audit (2026-10-28)

Audited at commit `4ce7558` (master), immediately after the security deep-review
fix pass (`docs/code-review-2026-10-28.md` — not repeated here). Scope: dashboard
UX & API, docs-vs-reality, CLI product experience, architecture, test honesty,
install/onboarding/upgrade. Read-only; every finding verified with file:line.

> **FIX PASS 2026-10-28 (same day, commits `ba3e20e`…`870db2e`, pushed):**
> every CRITICAL/HIGH plus the valuable MEDIUMs and the documentation debt are
> fixed. Local suite grew 68 → 112 passing (macOS; the 8 pb-binary fixture
> errors are environmental — CI Linux runs the full ~120 green). `cli.py` was
> split into the `cli/` package and a conservative ruff gate (E9/F63/F7/F82)
> was added to CI — which immediately caught an undefined-name bug the
> compiler and tests had missed.

---

## Verdict

Promising beta CLI wearing a v0.1 dashboard: the command surface matched README
almost 1:1 and the real-PocketBase backup tests were genuinely good, but the
product broke its own contract where users trusted it most — promised automatic
backups never enabled, installer dead on the documented minimum OS, security
fixes never reaching existing installs, and a dashboard with a silently
discarded form field and an unreachable backup feature.

---

## What was found → what shipped

### CRITICAL
- **Auto-backups never actually enabled on create** — only a state flag was
  written; PocketBase's scheduler was never configured. README promised it.
  → `create_instance` now calls `configure_auto_backup` with the
  `defaults.auto_backups_*` config, records honest state flags, and shows a
  warning panel with the manual fix when it fails.
- **Installer fails on stock Ubuntu 22.04** — unconditional
  `--break-system-packages` (pip ≥ 23 only; 22.04 ships 22.0.2).
  → flag detection in install.sh and in `selfupdate.perform_update`.
- **Security fixes never reach existing installs** — nothing regenerated unit
  files after `pm self-update`, so pre-2026-10-28 installs stayed on 0.0.0.0
  and the legacy root-crontab SFTP job persisted forever.
  → new `pm doctor` migration pass (rebind units to the configured
  `bind_host`, crontab cleanup, confirmation before restarts), nudged by
  `pm info` and `pm self-update` until run.

### HIGH (selection)
- Create partial-failure orphans (dir + unit file, unregistered) → best-effort
  cleanup + re-raise; `pm remove` now reports surviving unit files/Pangolin
  resources instead of swallowing them.
- No way to attach a Pangolin resource to an existing instance →
  `pm modify <name> --domain` (creates the resource, honest failure messages).
- `pm update-all`: no confirmation, restarted already-current instances →
  confirms (or `--yes`), skips current, exits non-zero on failure; same
  exit-code rule for `backup-all`/`healthcheck` (the systemd timer can now
  signal failure); `--yes` on `restore`/`self-update`.
- Stopped-instance guidance loop ("run pm credentials" ↔ "create superadmin")
  → reachability check says `pm start`; restore/backup failures carry reasons
  (`create_backup_ex`/`restore_backup_ex`).
- Dashboard: `subdomain` form field silently dropped by the API → wired
  (derives the domain via `pangolin.default_domain`); backups dead-end without
  credentials → inline credentials form calling the previously orphan
  `POST /credentials`; 30 s poll wiped loaded details → detail cache; no
  in-flight feedback on action buttons; health checks were O(all instances)
  → per-instance endpoint; keyboard/ARIA/focus/contrast/clipping fixes; 401
  stops polling with a reload banner.
- Broken-by-example README docs (`auth --password <pw>`,
  `sftp-config --password`, `--download` destination, "restore backups"
  dashboard claim, undocumented `pm modify`, self-contradicting 0600/0660)
  → all corrected; install.sh Pangolin step now collects `api_url` +
  `default_domain` and fails per-key instead of via a dead `&&` chain.

### Regressions/gaps found in the *excluded* (already-fixed) areas
- cron converter: `0-7` day-of-week produced `Sun..Sun` (weekly) instead of
  daily → 7-day ranges now convert to `*`.

### Architecture & tests
- `cli.py` (2329 lines) split into `cli/` package: `common.py` (group, console,
  formatters, auth helpers, deduplicated `_latest_backup_key` ×4 and
  `_upload_and_prune` ×3) + 7 command modules; `pm --help` byte-identical.
- 37 new unit tests (service-unit template + env sanitization, update rollback,
  SFTP prune with fake paramiko, cron timer units, create cleanup/auto-backup,
  doctor) and every tautological test strengthened. Dead code removed
  (`format_health_table`, `pangolin.list_domains`, `selfupdate.get_current_version`,
  `app.run_server`, unused `mock_systemd` fixture).
- ruff gate added; first run caught `_pip_break_system_packages_supported`
  being called but never defined in `selfupdate.py`.

## Known limitations / deliberately not done
- **PMError hierarchy** — skipped: the `*_ex` functions already deliver
  user-facing error reasons; a raise-based rewrite would churn the
  real-PocketBase integration tests for aesthetic gain. Revisit if core
  modules diverge again.
- `bind_host` parsing in `doctor`/`modify_service` is IPv4-only (config
  documents `bind_host` as an IP; hostnames fall through to the regenerate
  path).
- Dashboard restore/download/delete backup actions remain CLI-only; backups
  list still unpaginated (fine at current scale).
