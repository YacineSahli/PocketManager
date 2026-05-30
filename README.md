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
- Pangolin resource authentication management (view status, set/remove password)
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

PocketManager separates **configuration** (static settings) from **state** (mutable runtime data):

| Path | Purpose |
|------|---------|
| `~/pocketmanager/` | Tool installation directory |
| `/etc/pocketmanager/config.json` | Global configuration |
| `/var/lib/pocketmanager/instances.json` | Instance state (registered instances, versions, ports) |
| `~/.pocketmanager/cache/` | Downloaded PocketBase binaries cache |
| `~/pocketbases/pocketbase-<name>/` | Instance data directories |
| `/etc/systemd/system/pocketbase-<name>.service` | Systemd service files |

Both paths can be overridden with environment variables:

| Variable | Default | Purpose |
|----------|---------|---------|
| `POCKETMANAGER_HOME` | `/etc/pocketmanager` | Config directory (`config.json`) |
| `POCKETMANAGER_STATE_DIR` | `/var/lib/pocketmanager` | State directory (`instances.json`) |

---

## Quick Install (Automated)

```bash
curl -sSL https://raw.githubusercontent.com/yacinesahli/PocketManager/master/install.sh | bash
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

PocketManager stores its configuration in `/etc/pocketmanager/config.json`. The full structure with defaults is shown below:

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
    "dashboard_url": "https://apps.example.com",
    "api_url": "http://localhost:3003/v1",
    "api_key": "",
    "org_id": "",
    "default_domain_id": "",
    "default_domain": "",
    "subdomain_suffix": "",
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
| `dashboard_password` | Password to protect the web dashboard (required — you will be prompted on first launch) |
| `port_range` | Min/max port range for automatic port allocation |
| `pangolin.api_url` | Pangolin **Integration API** base URL. Use `http://localhost:3003/v1` for same-host (recommended) or `https://api.example.com/v1` for public access |
| `pangolin.api_key` | API key for your Pangolin instance |
| `pangolin.org_id` | Organization ID in Pangolin (text ID, e.g. `yacine`) |
| `pangolin.default_domain_id` | Domain ID to use for subdomain-based instances (e.g. `domain1`) |
| `pangolin.default_domain` | **Base domain** used to build public URLs. This is the root domain (e.g. `yacinesahli.com`), **not** including any subdomain suffix |
| `pangolin.subdomain_suffix` | Optional suffix appended to subdomains. If your resources live at `*.apps.example.com`, set this to `apps` and `default_domain` to `example.com` |
| `pangolin.site_id` | Site ID for Pangolin resource creation (the site connected to your VPS) |
| `defaults.auto_backups_enabled` | Enable automatic daily backups |
| `defaults.auto_backups_cron` | Cron schedule for automatic backups |
| `defaults.auto_backups_max_keep` | Maximum number of automatic backups to retain |

View and edit configuration from the CLI:

```bash
pm config                          # View current configuration (secrets masked)
pm config --reveal                 # View configuration with secrets visible
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
| `pm ls` | List all instances with status |
| `pm start <name>` | Start a stopped instance |
| `pm stop <name>` | Stop a running instance |
| `pm restart <name>` | Restart an instance |
| `pm remove <name>` | Remove an instance (with double confirmation) |
| `pm remove <name> --keep-data` | Remove instance but keep data on disk |
| `pm remove <name> --force` | Skip confirmation prompts |
| `pm status <name>` | Show detailed instance information (includes Pangolin auth status) |
| `pm auth <name>` | Show Pangolin authentication status for an instance |
| `pm auth <name> --password <pw>` | Set password authentication on the Pangolin resource |
| `pm auth <name> --no-password` | Remove password authentication from the Pangolin resource |
| `pm info` | Show system and PocketManager information |

### Logs and Health

| Command | Description |
|---------|-------------|
| `pm logs <name>` | View the last 100 log lines |
| `pm logs <name> -f` | Follow (tail) logs in real time |
| `pm logs <name> -n 50` | Show the last 50 log lines |
| `pm healthcheck` | Check health of all instances |

### Backups

> **Prerequisite:** PocketBase backup endpoints require superuser authentication.
> Before using backup commands, configure the PocketBase superadmin credentials:
>
> ```bash
> pm credentials <name>
> ```
>
> You will be prompted for the superadmin email and password (the same ones you
> set up via the PocketBase Admin UI at `http://localhost:<port>/_/`). Credentials
> are verified against the live instance and stored in the instance state file
> (owner-only readable, `0600` permissions).
>
> **Tip:** Create a dedicated backup superadmin in the PocketBase Admin UI (e.g.
> `pm-backup@instance`) and use that for `pm credentials`. This keeps your personal
> superadmin account separate from automated backup operations.

| Command | Description |
|---------|-------------|
| `pm credentials <name>` | Set or update PocketBase superadmin credentials |
| `pm backup <name>` | Create a backup of an instance |
| `pm backup <name> --download` | Create a backup and download it locally |
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
| `pm dashboard --stop` | Stop a background dashboard |
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

The dashboard requires a password to start. If no password is configured, you will be prompted to set one:

```bash
# Set it in advance (recommended for daemon mode)
pm config set dashboard_password YOUR_PASSWORD
```

When starting interactively without a password, you will be prompted. In daemon mode (`--daemon`), a password **must** be set beforehand.

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

### Enabling the Integration API

> **Required:** PocketManager communicates with Pangolin via the **Integration API**, which is **not enabled by default**. You must enable it before configuring PocketManager.
>
> Follow the official guide: [Pangolin Integration API Setup](https://docs.pangolin.net/self-host/advanced/integration-api)

In your Pangolin `config/config.yml`, add:

```yaml
flags:
  enable_integration_api: true

server:
  integration_port: 3003  # default port; change if needed
```

#### Exposing the API

You have two options for making the Integration API accessible:

**Option A — Local access (recommended for same-host setup)**

If PocketManager and Pangolin run on the same host, expose the integration port on localhost only. Add a port mapping to the `pangolin` service in your `docker-compose.yml`:

```yaml
pangolin:
  container_name: pangolin
  ports:
    - "127.0.0.1:3003:3003"  # Integration API (localhost only)
  # ... rest of config
```

Then use `http://localhost:3003/v1` as your `pangolin.api_url`.

> **Note:** If you don't expose the port, you can also use the container's internal Docker IP directly (e.g. `http://172.19.0.4:3003/v1`). You can find it with `docker inspect pangolin --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'`. Be aware that the container IP may change when the container is recreated.

**Option B — Public access via reverse proxy**

Expose the integration API through your reverse proxy. For example, with Traefik, add a router pointing to `http://pangolin:3003` on a subdomain like `api.apps.yacinesahli.com`. The API base URL will be `https://api.apps.yacinesahli.com/v1`.

You can verify the integration API is running by visiting `http://<your-api-url>/v1/` — it should return `{"message":"Healthy"}`. The full OpenAPI spec is available at `/v1/openapi.json`.

### Creating a Pangolin API Key

You need a **Pangolin API Key** with resource management permissions. A Root API Key (created from Admin → API Keys) allows PocketManager to auto-discover your org, domains, and sites. An Org-level key also works if you already know your org ID.

1. Open your **Pangolin dashboard** in a browser
2. Navigate to **Admin → API Keys** (for a Root key) or **Organization → API Keys** (for an Org key)
3. Click **Create API Key**
4. Give it a descriptive name (e.g. `"PocketManager"`)
5. Grant it **resource management** permissions (create/delete resources, set targets)
6. **Copy the key immediately** — it will not be shown again

You will also need three IDs from your Pangolin dashboard:

| ID | Where to Find It |
|----|------------------|
| `org_id` | Visible in the dashboard URL: `https://apps.example.com/<org_id>/...` |
| `default_domain_id` | Organization → Domains (e.g. `domain1`) |
| `site_id` | Sites page — the site connected to your VPS (e.g. `1`) |

### Subdomain Suffix

If your Pangolin domain uses a subdomain pattern (e.g. resources are at `*.apps.example.com` instead of `*.example.com`), set the `subdomain_suffix` config. This works together with `default_domain`:

| Config | Value | Purpose |
|--------|-------|---------|
| `default_domain` | `example.com` | The root domain in Pangolin |
| `subdomain_suffix` | `apps` | Appended to subdomains when creating resources |

```bash
pm config set pangolin.default_domain example.com
pm config set pangolin.subdomain_suffix apps
```

With these settings, `pm create myapp -s myapp` will:
1. Create the Pangolin resource with subdomain `myapp.apps`
2. Display the URL as `https://myapp.apps.example.com`

If your resources are at `*.example.com` directly (no suffix), leave `subdomain_suffix` empty and set `default_domain` to `example.com`.

### Configuration

Set all values in PocketManager:

```bash
# Integration API — use localhost for same-host (recommended) or public URL
pm config set pangolin.api_url http://localhost:3003/v1
pm config set pangolin.api_key YOUR_API_KEY
pm config set pangolin.org_id YOUR_ORG_ID             # text ID from Pangolin (e.g. "yacine")
pm config set pangolin.default_domain_id YOUR_DOMAIN_ID # domain ID from Pangolin (e.g. "domain1")
pm config set pangolin.site_id YOUR_SITE_ID             # numeric site ID (e.g. "1")
pm config set pangolin.default_domain example.com        # base domain (root, no subdomain prefix)
pm config set pangolin.subdomain_suffix apps             # optional: if resources are at *.apps.example.com
pm config set pangolin.target_ip 172.19.0.1              # Docker bridge gateway IP (or 127.0.0.1)
```

> **Finding your IDs:** The `org_id` is a text identifier visible in your Pangolin dashboard URL. The `default_domain_id` and `site_id` can be found on the Organization → Domains and Sites pages respectively. You can also query the Integration API:
>
> ```bash
> # List domains for your org (replace YOUR_ORG_ID and YOUR_API_KEY)
> curl -H "Authorization: Bearer YOUR_API_KEY" http://localhost:3003/v1/org/YOUR_ORG_ID/domains
>
> # List sites for your org
> curl -H "Authorization: Bearer YOUR_API_KEY" http://localhost:3003/v1/org/YOUR_ORG_ID/sites
> ```

### How It Works

- When creating an instance with `pm create myapp -d api.example.com`, PocketManager creates a Pangolin resource that proxies `https://api.example.com` to the instance's local port.
- When creating with a subdomain (`-s myapp`), PocketManager uses the configured `default_domain` and `subdomain_suffix` to build the full URL (e.g. `https://myapp.apps.yacinesahli.com`).
- When removing an instance, the corresponding Pangolin resource is also deleted.

> **Note:** Pangolin enables SSO authentication by default on new resources. This means visitors must authenticate via Pangolin before reaching your PocketBase instance. For PocketBase instances that need to be publicly accessible (e.g. serving a public API), use `pm auth <name> --no-password` to remove password auth or manage auth via the Pangolin dashboard.

### Resource Authentication

When a Pangolin resource is created, SSO authentication is enabled by default. Use the `pm auth` command to view and manage authentication:

```bash
pm auth myapp                          # View current auth status
pm auth myapp --password s3cret        # Set password authentication
pm auth myapp --no-password            # Remove password authentication
```

The `pm status <name>` command also shows the current authentication status in its output panel.

To skip Pangolin integration for a single instance, use the `--no-pangolin` flag:

```bash
pm create myapp -p 8110 --no-pangolin
```

---

## Backups

PocketManager leverages the [PocketBase backup API](https://pocketbase.io/docs/api-backups/) to create, list, download, restore, and delete backups of your instances. All backup endpoints require PocketBase superuser authentication.

### Where Backups Are Stored

Backup archives are **zip files** stored inside each instance's data directory:

```
~/pocketbases/pocketbase-<name>/pb_data/backups/
```

The file naming convention is `pb_backup_acme_<YYYYMMDDHHMMSS>.zip` (auto-generated by PocketBase). For example:

```
~/pocketbases/pocketbase-myapp/pb_data/backups/
  pb_backup_acme_20260530143000.zip
  pb_backup_acme_20260531030000.zip
```

> **Note:** The `acme` part in the name is PocketBase's internal default identifier. The timestamp uses UTC.

When you use `--download`, the backup file is additionally copied to the **instance directory** (not the backups subfolder):

```
~/pocketbases/pocketbase-myapp/pb_backup_acme_20260530143000.zip
```

### First-Time Setup

After creating an instance, set up the PocketBase superadmin account via the Admin UI:

1. Visit `http://localhost:<port>/_/` in your browser
2. Create the superadmin account (email + password)

Then register those credentials with PocketManager:

```bash
pm credentials myapp
```

You will be prompted for the superadmin email and password. PocketManager verifies them against the live instance and stores them securely (owner-only `0600` permissions in the instance state file). Once configured, all backup commands authenticate automatically.

> **Tip:** It's recommended to create a **dedicated backup superadmin** (e.g. `pm-backup@myapp`) in the PocketBase Admin UI and use that for `pm credentials` instead of your personal account. This way:
> - Changing your main superadmin password won't break automated backups
> - If the stored credentials are ever compromised, you can simply delete the dedicated user
> - PocketBase logs will clearly distinguish backup operations from manual admin actions

### Creating Backups

```bash
pm backup myapp                      # Create a backup with an auto-generated name
pm backup myapp --name before-update # Create a backup with a custom name
```

This creates a new zip archive in `pb_data/backups/` containing a full snapshot of the PocketBase database (`data.db`), uploaded files (`storage/`), and settings.

### Listing Backups

```bash
pm backups myapp
```

This displays a table with each backup's **key** (filename), **modified** timestamp, and **size**. The key is used to identify a backup for restore or download operations.

### Downloading Backups

```bash
pm backup myapp --download                      # Create and download the latest backup
pm backup myapp --download --name before-update # Create a named backup and download it
```

The `--download` flag creates a backup and then downloads it to the instance directory (`~/pocketbases/pocketbase-<name>/`). The downloaded file is a standard zip archive that you can copy off-server for disaster recovery.

### Restoring Backups

```bash
pm restore myapp <backup_key>
```

Use the backup key shown by `pm backups` (e.g. `pb_backup_acme_20260530143000.zip`). The restore process:

1. **Replaces** the current `pb_data` with the backup data
2. **Restarts** the instance automatically to load the restored data

> **Warning:** Restore is destructive — the current database is replaced. Always create a fresh backup before restoring:
>
> ```bash
> pm backup myapp              # safety backup of current state
> pm restore myapp <key>       # restore from a specific backup
> ```

### Deleting Backups

```bash
# Currently available via the PocketBase API directly:
curl -X DELETE http://localhost:<port>/api/backups/<backup_key> \
  -H "Authorization: <token>"
```

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

This example runs backups every day at 03:00 UTC and retains the last 7 backups (oldest are automatically deleted when the limit is exceeded).

### Backup and Restore Workflow Example

```bash
# 1. Set up credentials (one-time)
pm credentials myapp

# 2. Create a backup before making changes
pm backup myapp
# Output: Backup created successfully for 'myapp'.

# 3. List available backups
pm backups myapp
# Output:
# Key                                Modified                Size
# pb_backup_acme_20260530143000.zip  2026-05-30 14:30:00     2.1 MB

# 4. Download a copy off-server
pm backup myapp --download

# 5. Restore if something goes wrong
pm restore myapp pb_backup_acme_20260530143000.zip
# Output: Backup restored. Instance will restart automatically.
```

### Best Practices

- **Always back up before updating** an instance's PocketBase version
- **Always back up before restoring** a different backup (current data is replaced)
- **Download backups off-server** periodically for disaster recovery — server-level failures (disk corruption, accidental deletion) will also delete local backups
- **Test restore on a staging instance** before relying on it in production
- **Use a dedicated backup superadmin** to isolate credentials from personal accounts

### Off-Site Backups via SFTP

PocketManager can automatically push backup archives to a remote SFTP server (e.g. [Hetzner Storagebox](https://www.hetzner.com/storage/storage-box)). This protects against data loss if the VPS disk fails or is accidentally deleted.

#### Setup

1. **Configure the SFTP connection:**

```bash
pm sftp-config \
  --host u123456.your-storagebox.de \
  --port 23 \
  --username u123456-sub1
```

2. **Set the password (you'll be prompted securely):**

```bash
pm sftp-config --password
```

   Alternatively, use SSH key-based auth:

```bash
pm sftp-config --private-key ~/.ssh/id_storagebox
```

3. **Test the connection:**

```bash
pm sftp-config --test
```

4. **Enable off-site backups:**

```bash
pm sftp-config --enable
```

5. **Review the current configuration:**

```bash
pm sftp-config
```

#### Remote File Layout

Backups are organized in per-instance folders on the remote server:

```
/backups/
  myapp/
    pb_backup_acme_20260530143000.zip
    pb_backup_acme_20260531030000.zip
  otherapp/
    pb_backup_acme_20260530150000.zip
```

#### Uploading Backups

**Upload the latest backup after creation:**

```bash
pm backup myapp --push
```

**Upload a specific backup:**

```bash
pm push-backup myapp pb_backup_acme_20260530143000.zip
```

**Upload the most recent backup (no key needed):**

```bash
pm push-backup myapp
```

#### Listing Remote Backups

```bash
pm backups myapp --remote
```

This displays a table of backup files stored on the SFTP server with filename, modified date, and size.

#### Automatic Pruning

When `max_remote_backups` is set (default: 30), PocketManager automatically deletes the oldest remote backups when new ones are uploaded. Configure the limit:

```bash
pm sftp-config --max-remote-backups 14
```

#### Full Configuration Reference

| Option | Default | Description |
|--------|---------|-------------|
| `--host` | _(empty)_ | SFTP server hostname |
| `--port` | `22` | SFTP port |
| `--username` | _(empty)_ | SFTP username |
| `--password` | _(empty)_ | SFTP password |
| `--private-key` | _(empty)_ | Path to SSH private key |
| `--remote-path` | `/backups` | Remote directory root |
| `--max-remote-backups` | `30` | Max backups per instance |
| `--enable / --disable` | `disabled` | Enable or disable SFTP |

All settings can also be configured directly in `config.json`:

```json
{
  "sftp": {
    "enabled": true,
    "host": "u123456.your-storagebox.de",
    "port": 23,
    "username": "u123456-sub1",
    "password": "your-password",
    "private_key_path": "",
    "remote_path": "/backups",
    "max_remote_backups": 30
  }
}
```

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
    sftp.py              # SFTP off-site backup storage
    health.py             # Health checking
    selfupdate.py         # Self-update mechanism
  dashboard/
    app.py                # Flask app factory
    api.py                # REST API endpoints
    auth.py               # Dashboard authentication
    templates/
      dashboard.html      # Single-page dashboard
```

- **cli.py** -- Entry point. Defines all `pm` commands using [Click](https://click.palletsprojects.com/).
- **core/config.py** -- Manages `config.json` with dot-notation get/set, deep-merging with defaults, and atomic file writes.
- **core/state.py** -- Persists instance metadata (name, port, version, status) in `instances.json`.
- **core/instance.py** -- Orchestrates the full instance lifecycle (create, start, stop, remove, update, migrate).
- **core/systemd.py** -- Generates, installs, enables, and manages systemd service units.
- **core/pangolin.py** -- HTTP client for the Pangolin API (create/delete proxy resources, manage authentication).
- **core/backup.py** -- Wraps the PocketBase backup REST endpoints.
- **core/sftp.py** -- SFTP client for off-site backup storage (upload, list, delete, prune). Supports password and key-based auth.
- **core/health.py** -- Sends HTTP health probes to all instances and reports results.
- **core/selfupdate.py** -- Checks GitHub Releases and applies updates via git.
- **dashboard/** -- Flask-based web dashboard with a single-page frontend, REST API, and optional password auth.

---

## Contributing

Pull requests are welcome. This project is released under the [MIT License](LICENSE).

---

## License

MIT License -- see the [LICENSE](LICENSE) file for details.
