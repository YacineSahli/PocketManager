#!/usr/bin/env bash
set -euo pipefail

# ─── Colors ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m' # No Color

info()    { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*"; }
step()    { echo -e "\n${BOLD}[$1/$TOTAL_STEPS]${NC} $2"; }

# ─── Config ───────────────────────────────────────────────────────────────────
REPO_URL="git+https://github.com/YacineSahli/PocketManager.git@master"
POCKETBASES_DIR="$HOME/pocketbases"
CACHE_DIR="$HOME/.pocketmanager/cache"
CONFIG_DIR="/etc/pocketmanager"
STATE_DIR="/var/lib/pocketmanager"
TOTAL_STEPS=6

# ─── Banner ──────────────────────────────────────────────────────────────────
print_banner() {
    echo -e "${GREEN}"
    echo "  ╔═══════════════════════════════════════╗"
    echo "  ║       PocketManager Installer v0.1.0  ║"
    echo "  ║                                       ║"
    echo "  ║   Manage PocketBase on your VPS       ║"
    echo "  ╚═══════════════════════════════════════╝"
    echo -e "${NC}"
}

# ─── Step 1: Check requirements ─────────────────────────────────────────────
check_requirements() {
    step 1 "Checking requirements..."

    local missing=()

    # Python 3.10+
    if ! command -v python3 &>/dev/null; then
        missing+=("python3")
    else
        local py_version
        py_version=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
        if ! python3 -c "import sys; exit(0 if sys.version_info >= (3, 10) else 1)"; then
            error "Python 3.10+ is required (found ${py_version})"
            missing+=("python3 (>=3.10)")
        else
            info "Python ${py_version} found"
        fi
    fi

    # pip
    if ! python3 -m pip --version &>/dev/null; then
        missing+=("python3-pip")
    else
        info "pip found"
    fi

    # git (needed by pip to install from git URL)
    if ! command -v git &>/dev/null; then
        missing+=("git")
    else
        info "git $(git --version | awk '{print $3}') found"
    fi

    # curl
    if ! command -v curl &>/dev/null; then
        missing+=("curl")
    else
        info "curl found"
    fi

    # jq
    if ! command -v jq &>/dev/null; then
        missing+=("jq")
    else
        info "jq found"
    fi

    # systemd
    if [[ ! -d /run/systemd/system ]]; then
        missing+=("systemd")
    else
        info "systemd found"
    fi

    if [[ ${#missing[@]} -gt 0 ]]; then
        echo ""
        error "Missing required packages:"
        for pkg in "${missing[@]}"; do
            echo -e "  ${YELLOW}- ${pkg}${NC}"
        done
        echo ""
        echo -e "Install them with:"
        echo -e "  ${BOLD}sudo apt-get update && sudo apt-get install -y ${missing[*]}${NC}"
        exit 1
    fi

    # Check if running as root
    if [[ "$(id -u)" -eq 0 ]]; then
        warn "Running as root is not recommended."
        warn "Please run this script as a normal user with sudo access."
        exit 1
    fi

    info "All requirements met."
}

# ─── Step 2: Install PocketManager via pip ────────────────────────────────────
install_pm() {
    step 2 "Installing PocketManager..."

    # Install for the current user (--user flag)
    if python3 -m pip install --user --break-system-packages --force-reinstall --no-cache-dir "$REPO_URL"; then
        info "PocketManager installed successfully."
    else
        error "Failed to install PocketManager via pip."
        echo -e "  Try manually: ${BOLD}python3 -m pip install --user --break-system-packages $REPO_URL${NC}"
        exit 1
    fi

    # Ensure ~/.local/bin is in PATH
    local bin_dir="$HOME/.local/bin"
    if [[ ":$PATH:" != *":$bin_dir:"* ]]; then
        echo "" >> "$HOME/.bashrc"
        echo "# Added by PocketManager installer" >> "$HOME/.bashrc"
        echo "export PATH=\"\$HOME/.local/bin:\$PATH\"" >> "$HOME/.bashrc"
        info "Added ~/.local/bin to PATH in .bashrc"
    fi

    export PATH="$HOME/.local/bin:$PATH"
}

# ─── Step 3: Create system user and directories ──────────────────────────────
setup_user_and_dirs() {
    step 3 "Creating system user and directories..."

    # Create pocketbase system user
    if id pocketbase &>/dev/null; then
        info "System user 'pocketbase' already exists."
    else
        sudo useradd --system --shell /bin/false --home "$POCKETBASES_DIR" \
            --comment "PocketBase service user" pocketbase
        info "System user 'pocketbase' created."
    fi

    # Create base directories
    if [[ ! -d "$POCKETBASES_DIR" ]]; then
        sudo mkdir -p "$POCKETBASES_DIR"
        sudo chown "$(id -un):$(id -gn)" "$POCKETBASES_DIR"
        info "Created ${POCKETBASES_DIR}"
    else
        info "Directory ${POCKETBASES_DIR} already exists."
    fi

    if [[ ! -d "$CACHE_DIR" ]]; then
        mkdir -p "$CACHE_DIR"
        info "Created ${CACHE_DIR}"
    else
        info "Directory ${CACHE_DIR} already exists."
    fi

    # Create config directory (/etc/pocketmanager)
    if [[ ! -d "$CONFIG_DIR" ]]; then
        sudo mkdir -p "$CONFIG_DIR"
        sudo chown "$(id -un):$(id -gn)" "$CONFIG_DIR"
        info "Created ${CONFIG_DIR}"
    else
        info "Directory ${CONFIG_DIR} already exists."
    fi

    # Create state directory (/var/lib/pocketmanager)
    if [[ ! -d "$STATE_DIR" ]]; then
        sudo mkdir -p "$STATE_DIR"
        sudo chown "$(id -un):$(id -gn)" "$STATE_DIR"
        info "Created ${STATE_DIR}"
    else
        info "Directory ${STATE_DIR} already exists."
    fi
}

# ─── Step 4: Migrate existing instances ──────────────────────────────────────
migrate_existing() {
    step 4 "Checking for existing PocketBase instances..."

    local existing
    existing=$(find "$POCKETBASES_DIR" -maxdepth 1 -type d -name "pocketbase-*" 2>/dev/null || true)

    if [[ -z "$existing" ]]; then
        info "No existing PocketBase instances found."
        return
    fi

    echo ""
    echo -e "Found existing PocketBase instances:"
    while read -r dir; do
        echo -e "  ${YELLOW}$(basename "$dir")${NC}"
    done <<< "$existing"
    echo ""

    if ! [[ -t 0 ]]; then
        warn "Non-interactive terminal detected. Skipping migration."
        warn "Run 'pm migrate-existing' later to import these instances."
        return
    fi

    read -rp "Import them into PocketManager? [Y/n] " answer || answer=""
    answer="${answer:-Y}"

    if [[ "$answer" =~ ^[Yy]$ ]]; then
        pm migrate-existing
        info "Migration complete."
    else
        info "Skipping migration."
    fi
}

# ─── Step 5: Configure Pangolin (interactive) ────────────────────────────────
setup_pangolin() {
    step 5 "Pangolin reverse-proxy setup"

    local config_file="$CONFIG_DIR/config.json"

    # Check if Pangolin is already configured
    if [[ -f "$config_file" ]]; then
        local has_key has_org has_domain has_site
        has_key=$(jq -r '.pangolin.api_key // ""' "$config_file" 2>/dev/null)
        has_org=$(jq -r '.pangolin.org_id // ""' "$config_file" 2>/dev/null)
        has_domain=$(jq -r '.pangolin.default_domain_id // ""' "$config_file" 2>/dev/null)
        has_site=$(jq -r '.pangolin.site_id // ""' "$config_file" 2>/dev/null)

        if [[ -n "$has_key" && -n "$has_org" && -n "$has_domain" && -n "$has_site" ]]; then
            info "Pangolin is already fully configured. Skipping setup."
            return
        fi
    fi

    echo ""
    echo -e "  ${BOLD}Pangolin${NC} enables automatic public HTTPS URLs for your instances."
    echo -e "  You can configure it now or skip and set it up later with ${BOLD}pm config set${NC}."
    echo ""
    echo -e "  To find the required values, open your ${BOLD}Pangolin dashboard${NC}:"
    echo -e "    • ${BOLD}API Key${NC}  → Organization → API Keys → Create"
    echo -e "    • ${BOLD}Org ID${NC}   → Organization settings"
    echo -e "    • ${BOLD}Domain ID${NC} → Organization → Domains"
    echo -e "    • ${BOLD}Site ID${NC}  → Sites page"
    echo ""

    # Non-interactive check
    if ! [[ -t 0 ]]; then
        warn "Non-interactive terminal detected. Skipping Pangolin setup."
        warn "Configure it later with: pm config set pangolin.api_key <value>"
        return
    fi

    read -rp "Configure Pangolin now? [y/N] " answer || answer=""
    answer="${answer:-N}"
    if [[ ! "$answer" =~ ^[Yy]$ ]]; then
        info "Skipping Pangolin setup. Configure it later with 'pm config set'."
        return
    fi

    echo ""

    # Collect values
    local api_key="" org_id="" domain_id="" site_id=""

    read -rp "  API Key: " api_key
    api_key="${api_key// /}"  # trim spaces
    if [[ -z "$api_key" ]]; then
        warn "No API key provided. Skipping Pangolin setup."
        return
    fi

    read -rp "  Organization ID: " org_id
    org_id="${org_id// /}"
    if [[ -z "$org_id" ]]; then
        warn "No org ID provided. Skipping Pangolin setup."
        return
    fi

    read -rp "  Domain ID: " domain_id
    domain_id="${domain_id// /}"
    if [[ -z "$domain_id" ]]; then
        warn "No domain ID provided. Skipping Pangolin setup."
        return
    fi

    read -rp "  Site ID: " site_id
    site_id="${site_id// /}"
    if [[ -z "$site_id" ]]; then
        warn "No site ID provided. Skipping Pangolin setup."
        return
    fi

    # Write values via pm config set
    pm config set pangolin.api_key "$api_key" 2>/dev/null && \
    pm config set pangolin.org_id "$org_id" 2>/dev/null && \
    pm config set pangolin.default_domain_id "$domain_id" 2>/dev/null && \
    pm config set pangolin.site_id "$site_id" 2>/dev/null

    if [[ $? -eq 0 ]]; then
        info "Pangolin configured successfully."
    else
        warn "Failed to write Pangolin config. Set it manually with 'pm config set'."
    fi
}

# ─── Step 6: Print success ──────────────────────────────────────────────────
print_success() {
    step 6 "Done!"

    echo ""
    echo -e "${GREEN}╔═══════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║          PocketManager installed successfully!    ║${NC}"
    echo -e "${GREEN}╚═══════════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "  ${BOLD}Quick start:${NC}"
    echo -e "    pm --help              Show all commands"
    echo -e "    pm ls                  List PocketBase instances"
    echo -e "    pm create myapp        Create a new instance"
    echo -e "    pm dashboard           Launch the web dashboard"
    echo -e "    pm self-update         Update to the latest version"
    echo ""

    # Check if Pangolin is configured
    local pangolin_ready="no"
    if [[ -f "$CONFIG_DIR/config.json" ]]; then
        local _pk _po _pd _ps
        _pk=$(jq -r '.pangolin.api_key // ""' "$CONFIG_DIR/config.json" 2>/dev/null)
        _po=$(jq -r '.pangolin.org_id // ""' "$CONFIG_DIR/config.json" 2>/dev/null)
        _pd=$(jq -r '.pangolin.default_domain_id // ""' "$CONFIG_DIR/config.json" 2>/dev/null)
        _ps=$(jq -r '.pangolin.site_id // ""' "$CONFIG_DIR/config.json" 2>/dev/null)
        if [[ -n "$_pk" && -n "$_po" && -n "$_pd" && -n "$_ps" ]]; then
            pangolin_ready="yes"
        fi
    fi

    if [[ "$pangolin_ready" == "yes" ]]; then
        echo -e "  ${BOLD}Pangolin:${NC} ${GREEN}configured ✓${NC}"
    else
        echo -e "  ${BOLD}Pangolin:${NC} ${YELLOW}not configured${NC} (instances will work on localhost only)"
        echo ""
        echo -e "  To enable public HTTPS URLs, configure Pangolin:"
        echo ""
        echo -e "    1. Open your Pangolin dashboard"
        echo -e "    2. Go to ${BOLD}Organization → API Keys${NC} and create an API key"
        echo -e "       (grant it resource management permissions)"
        echo -e "    3. Note your ${BOLD}org ID${NC}, ${BOLD}domain ID${NC}, and ${BOLD}site ID${NC}"
        echo -e "       (found in Organization settings, Domains, and Sites)"
        echo -e "    4. Run:"
        echo ""
        echo -e "       ${BOLD}pm config set pangolin.api_key YOUR_KEY${NC}"
        echo -e "       ${BOLD}pm config set pangolin.org_id YOUR_ORG_ID${NC}"
        echo -e "       ${BOLD}pm config set pangolin.default_domain_id YOUR_DOMAIN_ID${NC}"
        echo -e "       ${BOLD}pm config set pangolin.site_id YOUR_SITE_ID${NC}"
    fi
    echo ""
}

# ─── Main ────────────────────────────────────────────────────────────────────
main() {
    print_banner
    check_requirements
    install_pm
    setup_user_and_dirs
    migrate_existing
    setup_pangolin
    print_success
}

main "$@"
