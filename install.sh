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
REPO_URL="https://github.com/yacinesahli/PocketManager.git"
INSTALL_DIR="$HOME/pocketmanager"
VENV_DIR="$INSTALL_DIR/.venv"
POCKETBASES_DIR="/home/ubuntu/pocketbases"
CACHE_DIR="/home/ubuntu/.pocketmanager/cache"
TOTAL_STEPS=8

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

    # git
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

    # python3-venv / ensurepip (required for virtual environment creation)
    if ! python3 -c "import ensurepip" &>/dev/null; then
        warn "python3-venv not found, attempting to install..."
        local py_version
        py_version=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
        if sudo apt-get update -qq && sudo apt-get install -y -qq "python${py_version}-venv"; then
            info "python${py_version}-venv installed successfully."
        elif sudo apt-get install -y -qq "python3-venv"; then
            info "python3-venv installed successfully."
        else
            error "Failed to install python3-venv."
            echo -e "  Install it manually: ${BOLD}sudo apt-get install -y python${py_version}-venv${NC}"
            exit 1
        fi
    else
        info "python3-venv found"
    fi

    # Check if running as root
    if [[ "$(id -u)" -eq 0 ]]; then
        warn "Running as root is not recommended."
        warn "Please run this script as a normal user with sudo access."
        exit 1
    fi

    info "All requirements met."
}

# ─── Step 2: Clone or update repo ────────────────────────────────────────────
clone_or_update_repo() {
    step 2 "Setting up repository..."

    if [[ -d "$INSTALL_DIR" ]]; then
        info "Repository exists at ${INSTALL_DIR}, updating..."
        git -C "$INSTALL_DIR" pull --ff-only
    else
        info "Cloning repository to ${INSTALL_DIR}..."
        git clone "$REPO_URL" "$INSTALL_DIR"
    fi

    info "Repository ready."
}

# ─── Step 3: Create Python virtual environment ───────────────────────────────
create_venv() {
    step 3 "Creating Python virtual environment..."

    if [[ -d "$VENV_DIR" ]]; then
        warn "Virtual environment already exists, recreating..."
        rm -rf "$VENV_DIR"
    fi

    if ! python3 -m venv "$VENV_DIR"; then
        error "Failed to create virtual environment."
        warn "Attempting to install python3-venv and retry..."
        local py_version
        py_version=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
        if sudo apt-get update -qq && sudo apt-get install -y -qq "python${py_version}-venv"; then
            rm -rf "$VENV_DIR"
            python3 -m venv "$VENV_DIR"
        else
            error "Could not install python3-venv. Please run:"
            echo -e "  ${BOLD}sudo apt-get install -y python${py_version}-venv${NC}"
            echo -e "  Then re-run this installer."
            exit 1
        fi
    fi
    info "Virtual environment created at ${VENV_DIR}"
}

# ─── Step 4: Install dependencies ───────────────────────────────────────────
install_dependencies() {
    step 4 "Installing dependencies..."

    "$VENV_DIR/bin/pip" install --upgrade pip
    "$VENV_DIR/bin/pip" install -e "$INSTALL_DIR/"

    info "Dependencies installed."
}

# ─── Step 5: Create system user and directories ──────────────────────────────
setup_user_and_dirs() {
    step 5 "Creating system user and directories..."

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
}

# ─── Step 6: Migrate existing instances ──────────────────────────────────────
migrate_existing() {
    step 6 "Checking for existing PocketBase instances..."

    local existing
    existing=$(find "$POCKETBASES_DIR" -maxdepth 1 -type d -name "pocketbase-*" 2>/dev/null || true)

    if [[ -z "$existing" ]]; then
        info "No existing PocketBase instances found."
        return
    fi

    echo ""
    echo -e "Found existing PocketBase instances:"
    echo "$existing" | while read -r dir; do
        echo -e "  ${YELLOW}$(basename "$dir")${NC}"
    done
    echo ""

    read -rp "Import them into PocketManager? [Y/n] " answer
    answer="${answer:-Y}"

    if [[ "$answer" =~ ^[Yy]$ ]]; then
        export PATH="$VENV_DIR/bin:$PATH"
        pm migrate-existing
        info "Migration complete."
    else
        info "Skipping migration."
    fi
}

# ─── Step 7: Add to PATH ────────────────────────────────────────────────────
add_to_path() {
    step 7 "Configuring PATH..."

    local path_entry="export PATH=\"\$HOME/pocketmanager/.venv/bin:\$PATH\""
    local bashrc="$HOME/.bashrc"

    if grep -qF 'pocketmanager/.venv/bin' "$bashrc" 2>/dev/null; then
        info "PATH entry already exists in ${bashrc}."
    else
        echo "" >> "$bashrc"
        echo "# Added by PocketManager installer" >> "$bashrc"
        echo "$path_entry" >> "$bashrc"
        info "Added PATH entry to ${bashrc}."
    fi

    # Create symlink
    sudo ln -sf "$VENV_DIR/bin/pm" /usr/local/bin/pm
    info "Created symlink: /usr/local/bin/pm -> ${VENV_DIR}/bin/pm"
}

# ─── Step 8: Print success ──────────────────────────────────────────────────
print_success() {
    step 8 "Done!"

    echo ""
    echo -e "${GREEN}╔═══════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║          PocketManager installed successfully!    ║${NC}"
    echo -e "${GREEN}╚═══════════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "  Installation path: ${BOLD}${INSTALL_DIR}${NC}"
    echo ""
    echo -e "  ${BOLD}Quick start:${NC}"
    echo -e "    pm --help              Show all commands"
    echo -e "    pm list                List PocketBase instances"
    echo -e "    pm create myapp        Create a new instance"
    echo -e "    pm dashboard           Launch the web dashboard"
    echo ""
    echo -e "  ${BOLD}Next step:${NC}"
    echo -e "    pm config set pangolin.api_key YOUR_KEY"
    echo ""
    echo -e "  ${YELLOW}Note:${NC} Run ${BOLD}source ~/.bashrc${NC} or open a new terminal"
    echo -e "         to apply PATH changes."
    echo ""
}

# ─── Main ────────────────────────────────────────────────────────────────────
main() {
    print_banner
    check_requirements
    clone_or_update_repo
    create_venv
    install_dependencies
    setup_user_and_dirs
    migrate_existing
    add_to_path
    print_success
}

main "$@"
