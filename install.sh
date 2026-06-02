#!/usr/bin/env bash
set -euo pipefail

# ── Secondbrain Claude Code skill installer ──────────────────────────────────

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
BRAIN_CLI_PATH="$REPO_DIR/scripts/brain_cli.py"

echo "🧠 Secondbrain installer"
echo "   Repo: $REPO_DIR"
echo ""

# ── 1. Smoke-test the CLI ─────────────────────────────────────────────────────
echo "🔍 Smoke-testing CLI…"
if ! python3 "$BRAIN_CLI_PATH" stats > /dev/null 2>&1; then
    echo "❌ CLI smoke-test failed: python3 $BRAIN_CLI_PATH stats"
    echo "   Make sure dependencies are installed and the database is accessible."
    exit 1
fi
echo "✅ CLI OK"
echo ""

# ── 2. Choose scope ───────────────────────────────────────────────────────────
echo "Where should the hooks be installed?"
echo "  1) Personal scope  (~/.claude/settings.json)"
echo "  2) Project scope   (.claude/settings.json  — current directory)"
printf "Enter 1 or 2 [default: 1]: "
read -r SCOPE_CHOICE
SCOPE_CHOICE="${SCOPE_CHOICE:-1}"

case "$SCOPE_CHOICE" in
    1)
        SETTINGS_DIR="$HOME/.claude"
        COMMANDS_DIR="$HOME/.claude/commands"
        SCOPE_LABEL="personal (~/.claude)"
        ;;
    2)
        SETTINGS_DIR="$(pwd)/.claude"
        COMMANDS_DIR="$(pwd)/.claude/commands"
        SCOPE_LABEL="project ($(pwd)/.claude)"
        ;;
    *)
        echo "❌ Invalid choice: $SCOPE_CHOICE"
        exit 1
        ;;
esac

SETTINGS_FILE="$SETTINGS_DIR/settings.json"
echo ""
echo "📁 Using $SCOPE_LABEL"

# ── 3. Merge hooks into settings.json ─────────────────────────────────────────
mkdir -p "$SETTINGS_DIR"

echo "🔧 Merging hooks into $SETTINGS_FILE…"

HOOK_CMD="python3 $BRAIN_CLI_PATH"

python3 - <<PYEOF
import json, sys, os

settings_file = """$SETTINGS_FILE"""
hook_cmd = """$HOOK_CMD"""

# Read existing settings (tolerate missing file or invalid JSON)
if os.path.exists(settings_file):
    try:
        with open(settings_file, "r") as f:
            settings = json.load(f)
    except (json.JSONDecodeError, ValueError):
        print("⚠️  Existing settings.json is invalid JSON — starting fresh.")
        settings = {}
else:
    settings = {}

if not isinstance(settings, dict):
    settings = {}

hooks = settings.setdefault("hooks", {})

installed_stop = False
installed_precompact = False

def hook_entry(cmd):
    return {"matcher": "", "hooks": [{"type": "command", "command": cmd}]}

# ── Stop hook ──
stop_list = hooks.setdefault("Stop", [])
already_stop = any(
    h.get("command") == hook_cmd
    for entry in stop_list
    for h in (entry.get("hooks") or [])
)
if already_stop:
    print("ℹ️  Stop hook already installed, skipping.")
else:
    stop_list.append(hook_entry(hook_cmd))
    installed_stop = True

# ── PreCompact hook ──
precompact_list = hooks.setdefault("PreCompact", [])
already_precompact = any(
    h.get("command") == hook_cmd
    for entry in precompact_list
    for h in (entry.get("hooks") or [])
)
if already_precompact:
    print("ℹ️  PreCompact hook already installed, skipping.")
else:
    precompact_list.append(hook_entry(hook_cmd))
    installed_precompact = True

with open(settings_file, "w") as f:
    json.dump(settings, f, indent=2)
    f.write("\n")

if installed_stop:
    print("✅ Stop hook added.")
if installed_precompact:
    print("✅ PreCompact hook added.")
PYEOF

# ── 4. Symlink commands/history.md ───────────────────────────────────────────
HISTORY_SRC="$REPO_DIR/commands/history.md"
HISTORY_LINK="$COMMANDS_DIR/history.md"

mkdir -p "$COMMANDS_DIR"

if [ -L "$HISTORY_LINK" ] && [ "$(readlink "$HISTORY_LINK")" = "$HISTORY_SRC" ]; then
    echo "ℹ️  commands/history.md symlink already in place, skipping."
elif [ -e "$HISTORY_LINK" ]; then
    echo "⚠️  $HISTORY_LINK exists and is not a symlink to $HISTORY_SRC — skipping."
else
    ln -s "$HISTORY_SRC" "$HISTORY_LINK"
    echo "✅ Symlinked commands/history.md → $HISTORY_LINK"
fi

# ── 5. Summary ────────────────────────────────────────────────────────────────
echo ""
echo "🎉 Installation complete!"
echo ""
echo "   Settings : $SETTINGS_FILE"
echo "   Commands : $HISTORY_LINK"
echo ""
echo "💡 Add this to your shell profile so other tools can find the CLI:"
echo ""
echo "   export SECONDBRAIN_CLI=$BRAIN_CLI_PATH"
echo ""
