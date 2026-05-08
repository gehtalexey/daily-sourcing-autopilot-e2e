#!/bin/bash
# Install Python dependencies in cloud sessions only.
# Local sessions skip this — developers manage their own venv.
#
# Why a SessionStart hook (not the cloud env's setup script)?
# The cloud env's setup script runs BEFORE the repo is cloned, so it can't see
# requirements.txt. SessionStart hooks run AFTER the clone, so they can.
# See: https://code.claude.com/docs/en/claude-code-on-the-web#install-dependencies-with-a-sessionstart-hook

# Skip in local sessions (CLAUDE_CODE_REMOTE is set to "true" only in cloud).
if [ "$CLAUDE_CODE_REMOTE" != "true" ]; then
  exit 0
fi

# Locate the project root via the script's own path. We can't trust
# $CLAUDE_PROJECT_DIR — in cloud bash sessions it's not always exported,
# and a `cd ""` would silently drop us at /. Self-anchored is reliable.
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$( dirname "$SCRIPT_DIR" )"
cd "$PROJECT_ROOT" || exit 1

# Force-reinstall cffi + cryptography first. The cloud sandbox ships a system
# `cryptography` in /usr/lib/python3/dist-packages that was built against a
# different Python ABI, breaking `import _cffi_backend` for any downstream
# package that uses cryptography (gspread → google-auth → cryptography). A
# fresh pip install into the user site-packages takes precedence in sys.path.
pip install --upgrade --force-reinstall --no-cache-dir cffi cryptography || true

# Install project deps. --no-cache-dir keeps cold installs predictable; we
# don't have control over the cache directory across cached environments.
pip install -r requirements.txt
