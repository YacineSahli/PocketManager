# PocketManager

A CLI tool and web dashboard to manage multiple PocketBase instances on a single Linux VPS.

![MIT License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)
![Experimental](https://img.shields.io/badge/status-experimental-orange.svg)

---

## Disclaimer

> **WARNING -- This is an experimental project.**
>
> **Always back up your PocketBase data before using PocketManager commands**, especially `remove`, `update`, and `restore`. The authors are **not responsible for any data loss** caused by this tool.
>
> Use `pm backup <name>` before any destructive operation. It is strongly recommended to test PocketManager on a **non-production server** first and verify that backups work correctly before relying on this tool with real data.

---

## Features

- Create, start, stop, restart, and remove PocketBase instances from the CLI
- Interactive and non-interactive instance creation with port, domain, subdomain, and environment variable support
- Automatic systemd service management for each instance
- [Pangolin](https://github.com/fosrl/pangolin) reverse proxy integration for automatic public URL creation
- Backup and restore via the PocketBase backup API
- Health checks for all running instances
- Web dashboard for browser-based management
- Self-updating mechanism (fetches latest release from GitHub)
- Automatic port allocation to avoid conflicts
- Import existing manually-created PocketBase instances
- Migrate-existing instances from the command line

---

## Requirements

- **OS:** Ubuntu 22.04+ (or any Debian-based Linux with systemd)
- **Python:** 3.10+
- **systemd** (for service management)
- **curl**, **git**, **jq** (standard CLI utilities)
- [Pangolin](https://github.com/fosrl/pangolin) reverse proxy (optional, required for automatic public URL creation)

---

## File Paths

PocketManager uses the following paths on your VPS:

| Path | Purpose |
|------|---------|
| `~/pocketmanager/` | Tool installation directory |
| `~/pocketmanager/config.json` | Global configuration |
| `~/pocketmanager/instances.json` | Instance state |
| `~/.pocketmanager/cache/` | Downloaded PocketBase binaries cache |
| `~/pocketbases/pocketbase-<name>/` | Instance data directories |
| `/etc/systemd/system/pocketbase-<name>.service` | Systemd service files |

---

## Quick Install (Automated)

```bash
curl -sSL https://raw.githubusercontent.com/yacinesahli/PocketManager/main/install.sh | bash
```

The installer will:

1. Clone the PocketManager repository into `~/pocketmanager`
2. Create a Python virtual environment
3. Install the package and its dependencies
4. Add the `pm` command to your PATH
5. Set up the `pocketbase` system user if it does not already exist

---

## Manual Installation

```bash
git clone https://github.com/yacinesahli/PocketManager.git ~/pocketmanager
cd ~/pocketmanager
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
pm --help
```

To make `pm` available without activating the virtual environment every time, add the following to your shell profile (`~/.bashrc` or `~/.zshrc`):

```bash
export PATH="$HOME/pocketmanager/.venv/bin:$PATH"
```

---

## Configuration

PocketManager stores its configuration in `~/pocketmanager/config.json`. The full structure with defaults is shown below:

```json
{
  "base_dir": "/home/ubuntu/pocketbases",
  "cache_dir": "/home/ubuntu/.pocketmanager/cache",
  "dashboard_port": 8888,
  "dashboard_password": "",
  "port_range": {
    "min": 8090,
    "max": 8999
  },
  "pangolin": {
    "dashboard_url": "https://apps.yacinesahli.com",
    "api_url": "https://apps.yacinesahli.com/api/v1",
    "api_key": "",
    "org_id": "",
    "default_domain_id": "",
    "site_id": ""
  },
  "defaults": {
    "auto_backups_enabled": true,
    "auto_backups_cron": "0 3 * * *",
    "auto_backups_max_keep": 7
  }
}
```

| Field | Description |
|-------|-------------|
| `base_dir` | Root directory where instance data folders are created |
| `cache_dir` | Directory for caching downloaded PocketBase binaries |
| `dashboard_port` | Port for the web dashboard (default: 8888) |
| `dashboard_password` | Password to protect the web dashboard (leave empty for no auth) |
| `port_range` | Min/max port range for automatic port allocation |
| `pangolin.api_key` | API key for your Pangolin instance |
| `pangolin.org_id` | Organization ID in Pangolin |
| `pangolin.default_domain_id` | Domain ID to use for subdomain-based instances |
| `pangolin.site_id` | Site ID for Pangolin resource creation |
| `defaults.auto_backups_enabled` | Enable automatic daily backups |
| `defaults.auto_backups_cron` | Cron schedule for automatic backups |
| `defaults.auto_backups_max_keep` | Maximum number of automatic backups to retain |

View and edit configuration from the CLI:

```bash
pm config                          # View current configuration
pm config set pangolin.api_key KEY # Set a Pangolin API key
pm config set pangolin.org_id ID   # Set organization ID
pm config set dashboard_password YOUR_PASSWORD  # Set dashboard password
pm config set port_range.min 7000  # Change port range start
```

---

## Usage -- CLI Commands

### Instance Management

| Command | Description |
|---------|-------------|
| `pm create <name>` | Create a new instance (interactive mode) |
| `pm create <name> -p 8110 -d example.com -s pb -e KEY=VAL` | Create with explicit options |
| `pm list` | List all instances with status |
| `pm ls` | Alias for `pm list` |
| `pm start <name>` | Start a stopped instance |
| `pm stop <name>` | Stop a running instance |
| `pm restart <name>` | Restart an instance |
| `pm remove <name>` | Remove an instance (with double confirmation) |
| `pm remove <name> --keep-data` | Remove instance but keep data on disk |
| `pm remove <name> --force` | Skip confirmation prompts |
| `pm status <name>` | Show detailed instance information |
| `pm info` | Show system and PocketManager information |

### Logs and Health

| Command | Description |
|---------|-------------|
| `pm logs <name>` | View the last 100 log lines |
| `pm logs <name> -f` | Follow (tail) logs in real time |
| `pm logs <name> -n 50` | Show the last 50 log lines |
| `pm healthcheck` | Check health of all instances |

### Backups

| Command | Description |
|---------|-------------|
| `pm backup <name>` | Create a backup of an instance |
| `pm backup <name> --download` | Create a backup and download it |
| `pm backup <name> --name mybackup` | Create a backup with a custom name |
| `pm backups <name>` | List all backups for an instance |
| `pm restore <name> <key>` | Restore an instance from a specific backup |

### Updates

| Command | Description |
|---------|-------------|
| `pm update <name>` | Update the PocketBase binary for an instance |
| `pm update <name> --version 0.22.0` | Update to a specific PocketBase version |
| `pm update-all` | Update all instances to the latest PocketBase version |
| `pm self-update` | Update PocketManager itself to the latest release |

### Other

| Command | Description |
|---------|-------------|
| `pm dashboard` | Start the web dashboard (default port 8888) |
| `pm dashboard --port 9999` | Start the dashboard on a custom port |
| `pm dashboard --daemon` | Run the dashboard in the background |
| `pm config` | View current configuration |
| `pm config set <key> <value>` | Set a configuration value |
| `pm migrate-existing` | Import manually-created PocketBase instances |

---

## Usage -- Dashboard

Start the web dashboard with:

```bash
pm dashboard
```

By default, the dashboard is available at `http://localhost:8888`. You can specify a custom port:

```bash
pm dashboard --port 9999
```

To run the dashboard in the background:

```bash
pm dashboard --daemon
```

### Setting a Password

It is recommended to protect the dashboard with a password:

```bash
pm config set dashboard_password YOUR_PASSWORD
```

If no password is set, the dashboard is accessible without authentication.

### Dashboard Features

- **Instance management** -- create, start, stop, restart, and remove instances
- **Health monitoring** -- view real-time health status for all instances
- **Backup management** -- create, list, and restore backups
- **Logs viewer** -- browse instance logs from the browser

---

## Instance Lifecycle

### Creating an Instance

When you run `pm create myapp`, PocketManager performs the following steps:

1. **Allocates a port** from the configured range (`port_range.min` to `port_range.max`)
2. **Downloads the PocketBase binary** (cached locally in `~/.pocketmanager/cache/`)
3. **Creates the instance directory** at `~/pocketbases/pocketbase-myapp/`
4. **Generates a systemd service file** at `/etc/systemd/system/pocketbase-myapp.service`
5. **Enables and starts the service**
6. **Creates a Pangolin resource** (if Pangolin is configured) with the appropriate domain or subdomain
7. **Registers the instance** in `instances.json`
8. **Runs a health check** to verify the instance is responding

### Instance Directory Structure

```
~/pocketbases/pocketbase-myapp/
  pocketbase          # PocketBase binary
  pb_data/            # Database and file storage
    data.db           # SQLite database
    storage/          # Uploaded files
    backups/          # Backup archives
  pb_hooks/           # JavaScript hooks
  pb_migrations/      # Database migration files
```

### Removing an Instance

When you run `pm remove myapp`, PocketManager:

1. Stops the systemd service
2. Disables and deletes the systemd service file
3. Removes the Pangolin resource (if applicable)
4. Deletes the instance directory (unless `--keep-data` is used)
5. Removes the instance entry from `instances.json`

The `remove` command requires double confirmation: it first asks whether you have backed up your data, then asks you to type the instance name to confirm deletion. Use `--force` to skip these prompts (not recommended).

---

## Pangolin Integration

PocketManager integrates with [Pangolin](https://github.com/fosrl/pangolin) to automatically create public HTTPS URLs for your PocketBase instances. When Pangolin is configured, each new instance can be assigned a domain or subdomain, and PocketManager will create the necessary proxy resources automatically.

### Configuration

1. Obtain an API key from your Pangolin dashboard
2. Note your organization ID, domain ID, and site ID
3. Set the values in PocketManager:

```bash
pm config set pangolin.api_key YOUR_API_KEY
pm config set pangolin.org_id YOUR_ORG_ID
pm config set pangolin.default_domain_id YOUR_DOMAIN_ID
pm config set pangolin.site_id YOUR_SITE_ID
```

### How It Works

- When creating an instance with `pm create myapp -d api.example.com`, PocketManager creates a Pangolin resource that proxies `https://api.example.com` to the instance's local port.
- When creating with a subdomain (`-s myapp`), PocketManager uses the configured `default_domain` to build `https://myapp.<default_domain>`.
- When removing an instance, the corresponding Pangolin resource is also deleted.

To skip Pangolin integration for a single instance, use the `--no-pangolin` flag:

```bash
pm create myapp -p 8110 --no-pangolin
```

---

## Backups

PocketManager leverages the [PocketBase backup API](https://pocketbase.io/docs/js-databases/#backup) to create, list, and restore backups of your instances.

### Creating Backups

```bash
pm backup myapp                      # Create a backup with an auto-generated name
pm backup myapp --name before-update # Create a backup with a custom name
```

Backups are stored inside the instance's `pb_data/backups/` directory on disk.

### Listing Backups

```bash
pm backups myapp
```

### Restoring Backups

```bash
pm restore myapp <backup_key>
```

The instance will be restarted automatically after a restore. The current database is replaced with the backup data.

### Automatic Backups

PocketManager supports automatic daily backups via cron. Configure this in your configuration:

```json
{
  "defaults": {
    "auto_backups_enabled": true,
    "auto_backups_cron": "0 3 * * *",
    "auto_backups_max_keep": 7
  }
}
```

This example runs backups every day at 03:00 and retains the last 7 backups.

### Best Practices

- **Always back up before updating** an instance's PocketBase version
- **Always back up before restoring** a different backup
- Periodically download backup archives off-server for disaster recovery
- Test restoring from a backup on a staging instance before relying on it

---

## Security

- Instances run as a dedicated `pocketbase` system user, not as root
- Systemd service files include hardening directives (`ProtectSystem`, `NoNewPrivileges`, and others) to limit the attack surface
- Health checks run via `ExecStartPost` to verify instances start correctly
- The web dashboard supports password-based authentication (configured via `dashboard_password`)
- **Recommendation:** In PocketBase's admin settings, restrict the superuser login to trusted IP addresses when possible

---

## Self-Updating

PocketManager can update itself to the latest release from GitHub:

```bash
pm self-update
```

The command:

1. Checks the GitHub Releases API for the latest version
2. Compares it against the currently installed version
3. If a newer version is available, displays the release notes and asks for confirmation
4. Pulls the update via `git pull` and reinstalls the package

You can check the current version at any time:

```bash
pm --version
```

---

## Architecture

```
pocketmanager/
  cli.py                  # Click CLI with all commands
  core/
    config.py             # Configuration management
    state.py              # Instance state (instances.json)
    ports.py              # Port allocation
    pocketbase.py         # Binary download and caching
    systemd.py            # Systemd service management
    instance.py           # Main instance orchestrator
    pangolin.py           # Pangolin API client
    backup.py             # Backup API wrapper
    health.py             # Health checking
    selfupdate.py         # Self-update mechanism
  dashboard/
    app.py                # Flask app factory
    api.py                # REST API endpoints
    auth.py               # Dashboard authentication
    templates/
      dashboard.html      # Single-page dashboard
    static/               # Static assets (CSS, JS)
```

- **cli.py** -- Entry point. Defines all `pm` commands using [Click](https://click.palletsprojects.com/).
- **core/config.py** -- Manages `config.json` with dot-notation get/set, deep-merging with defaults, and atomic file writes.
- **core/state.py** -- Persists instance metadata (name, port, version, status) in `instances.json`.
- **core/instance.py** -- Orchestrates the full instance lifecycle (create, start, stop, remove, update, migrate).
- **core/systemd.py** -- Generates, installs, enables, and manages systemd service units.
- **core/pangolin.py** -- HTTP client for the Pangolin API (create/delete proxy resources).
- **core/backup.py** -- Wraps the PocketBase backup REST endpoints.
- **core/health.py** -- Sends HTTP health probes to all instances and reports results.
- **core/selfupdate.py** -- Checks GitHub Releases and applies updates via git.
- **dashboard/** -- Flask-based web dashboard with a single-page frontend, REST API, and optional password auth.

---

## Contributing

Pull requests are welcome. This project is released under the [MIT License](LICENSE).

---

## License

MIT License -- see the [LICENSE](LICENSE) file for details.
