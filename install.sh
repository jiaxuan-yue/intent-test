#!/usr/bin/env bash
#
# intent-test skill — one-click installer
#
# Usage:
#   bash install.sh              # install to current project (project-level)
#   bash install.sh --global     # install to ~/.claude (user-level, all projects)
#   bash install.sh --path /path/to/project   # install to specific directory
#
set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Resolve script directory (where install.sh lives)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_SRC="$SCRIPT_DIR/.claude/skills"

# Defaults
INSTALL_MODE="project"
TARGET_DIR="."

# Parse arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --global)
            INSTALL_MODE="global"
            TARGET_DIR="$HOME"
            shift
            ;;
        --path)
            TARGET_DIR="$2"
            shift 2
            ;;
        --help|-h)
            echo "Usage:"
            echo "  bash install.sh                        Install to current directory"
            echo "  bash install.sh --global               Install to ~/.claude (all projects)"
            echo "  bash install.sh --path /path/to/dir    Install to specific directory"
            echo ""
            echo "Options:"
            echo "  --global     User-level install (skill available in all projects)"
            echo "  --path DIR   Install to a specific project directory"
            echo "  -h, --help   Show this help"
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            echo "Run 'bash install.sh --help' for usage."
            exit 1
            ;;
    esac
done

echo ""
echo -e "${BLUE}╔══════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║   intent-test Skill Installer            ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════╝${NC}"
echo ""

# Verify source files exist
if [[ ! -f "$SKILL_SRC/intent-test.md" ]]; then
    echo -e "${RED}Error: Skill files not found in $SKILL_SRC${NC}"
    echo "Make sure you're running this from the intent-test repo root."
    exit 1
fi

# Resolve target
TARGET_DIR="$(cd "$TARGET_DIR" 2>/dev/null && pwd)" || {
    echo -e "${RED}Error: Directory '$TARGET_DIR' does not exist.${NC}"
    exit 1
}

CLAUDE_DIR="$TARGET_DIR/.claude"
SKILL_DEST="$CLAUDE_DIR/skills"

echo -e "  Mode:    ${GREEN}$INSTALL_MODE${NC}"
echo -e "  Target:  ${GREEN}$TARGET_DIR${NC}"
echo ""

# Check if already installed
if [[ -f "$SKILL_DEST/intent-test.md" ]]; then
    echo -e "${YELLOW}⚠  Skill already installed at $SKILL_DEST/intent-test.md${NC}"
    read -rp "   Overwrite? [y/N] " confirm
    if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
        echo "   Skipped."
        exit 0
    fi
    echo ""
fi

# Create directories
mkdir -p "$SKILL_DEST/intent-test"

# Copy skill files
echo -e "  Copying skill files..."
cp "$SKILL_SRC/intent-test.md" "$SKILL_DEST/intent-test.md"
cp "$SKILL_SRC/intent-test/runner.py" "$SKILL_DEST/intent-test/runner.py"

# Copy CLAUDE.md only for project-level install and if it doesn't exist
if [[ "$INSTALL_MODE" == "project" ]]; then
    if [[ ! -f "$CLAUDE_DIR/CLAUDE.md" ]]; then
        cp "$SCRIPT_DIR/.claude/CLAUDE.md" "$CLAUDE_DIR/CLAUDE.md"
        echo -e "  ${GREEN}✓${NC} Copied CLAUDE.md"
    else
        echo -e "  ${YELLOW}⚠${NC} CLAUDE.md already exists, skipping (won't overwrite)"
    fi
fi

# Check Python
echo ""
echo -e "  Checking Python..."
if command -v python3 &>/dev/null; then
    PY_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
    echo -e "  ${GREEN}✓${NC} Python $PY_VERSION"
else
    echo -e "  ${YELLOW}⚠${NC} Python 3 not found. Install Python 3.8+ to use the runner script."
fi

# Check pydantic
if python3 -c "import pydantic" 2>/dev/null; then
    PYD_VERSION=$(python3 -c "import pydantic; print(pydantic.__version__)" 2>/dev/null || echo "unknown")
    echo -e "  ${GREEN}✓${NC} Pydantic $PYD_VERSION"
else
    echo -e "  ${YELLOW}⚠${NC} Pydantic not installed."
    echo -e "    Run: ${BLUE}pip install pydantic>=2.0${NC}"
fi

# Verify installation
echo ""
echo -e "  Verifying..."
INSTALLED_FILES=0
[[ -f "$SKILL_DEST/intent-test.md" ]] && ((INSTALLED_FILES++)) || true
[[ -f "$SKILL_DEST/intent-test/runner.py" ]] && ((INSTALLED_FILES++)) || true

if [[ $INSTALLED_FILES -eq 2 ]]; then
    echo -e "  ${GREEN}✓ All files installed successfully${NC}"
else
    echo -e "  ${RED}✗ Installation incomplete ($INSTALLED_FILES/2 files)${NC}"
    exit 1
fi

# Show installed structure
echo ""
echo -e "  ${BLUE}Installed files:${NC}"
echo "  $CLAUDE_DIR/"
echo "  ├── CLAUDE.md              (project context)"
echo "  └── skills/"
echo "      ├── intent-test.md     (skill definition)"
echo "      └── intent-test/"
echo "          └── runner.py      (helper script)"

# Done
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║   ✓ Installation complete!               ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════╝${NC}"
echo ""
echo -e "  Next step: Open Claude Code in ${BLUE}$TARGET_DIR${NC} and run:"
echo ""
echo -e "    ${BLUE}/intent-test${NC}"
echo ""
echo -e "  Or try a quick test:"
echo ""
echo -e "    ${BLUE}/intent-test mode=quick input=\"你好\"${NC}"
echo ""
