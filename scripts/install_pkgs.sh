#!/bin/bash
# Install Python dependencies in cloud sessions only.
# Local sessions skip this — developers manage their own venv.
#
# Why a SessionStart hook (not the cloud env's setup script)?
# The cloud env's setup script runs BEFORE the repo is cloned, so it can't see
# requirements.txt. SessionStart hooks run AFTER the clone, so they can.
# See: https://code.claude.com/docs/en/claude-code-on-the-web#install-dependencies-with-a-sessionstart-hook
set -e

if [ "$CLAUDE_CODE_REMOTE" != "true" ]; then
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR"
pip install -r requirements.txt
