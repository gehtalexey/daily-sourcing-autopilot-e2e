"""
Central config loader with env-var precedence.

Pattern: environment variable > config.json > default/None.

This lets the same code run locally (config.json present) and in cloud
environments like Claude Code Routines (env vars only, no file).

Dead config keys preserved in config.json for back-compat but unused in code:
phantombuster_api_key, phantombuster_agent_id, slack_app_token.
"""

import json
import os
from pathlib import Path
from typing import Any, Optional

_CONFIG_PATH = Path(__file__).parent.parent / 'config.json'
_cached_config: Optional[dict] = None


def _load_file() -> dict:
    """Read config.json once and cache. Returns {} if file missing or unreadable."""
    global _cached_config
    if _cached_config is not None:
        return _cached_config
    if _CONFIG_PATH.exists():
        try:
            with open(_CONFIG_PATH) as f:
                _cached_config = json.load(f)
                return _cached_config
        except Exception:
            pass
    _cached_config = {}
    return _cached_config


def get(
    key: str,
    env_var: Optional[str] = None,
    default: Any = None,
    required: bool = False,
) -> Any:
    """
    Get a config value with env-var precedence.

    Lookup order:
      1. os.environ[env_var]  (env_var defaults to key.upper())
      2. config.json[key]
      3. default

    Args:
        key: config.json key (e.g. 'crustdata_api_key').
        env_var: env var name. Defaults to key.upper().
        default: value if neither env nor config has it.
        required: raise RuntimeError if final value is empty/None.

    Returns:
        The resolved value, or default, or None.
    """
    if env_var is None:
        env_var = key.upper()
    value = os.environ.get(env_var)
    if not value:
        config = _load_file()
        value = config.get(key)
    if not value and default is not None:
        value = default
    if not value and required:
        raise RuntimeError(
            f"Missing required config: set env var {env_var} or config.json key '{key}'"
        )
    return value


def get_dict(key: str, env_var: Optional[str] = None) -> dict:
    """
    Get a dict-typed config value. If env var is set, it must be a JSON object.

    Use for nested config like 'filter_sheets'.
    """
    if env_var is None:
        env_var = key.upper()
    raw = os.environ.get(env_var)
    if raw:
        try:
            value = json.loads(raw)
            if isinstance(value, dict):
                return value
        except Exception:
            pass
    config = _load_file()
    value = config.get(key, {})
    return value if isinstance(value, dict) else {}


def get_google_creds_dict() -> Optional[dict]:
    """
    Return Google service-account credentials as a dict, ready for
    `Credentials.from_service_account_info(creds_dict, scopes=...)`.

    Lookup order:
      1. env GOOGLE_CREDENTIALS_JSON (full JSON string)  — for cloud envs
      2. file at config['google_credentials_file']        — for local dev
    """
    raw = os.environ.get('GOOGLE_CREDENTIALS_JSON')
    if raw:
        try:
            return json.loads(raw)
        except Exception as e:
            raise RuntimeError(
                f"GOOGLE_CREDENTIALS_JSON env var is not valid JSON: {e}"
            )
    config = _load_file()
    creds_file = config.get('google_credentials_file', 'google_credentials.json')
    creds_path = Path(__file__).parent.parent / creds_file
    if creds_path.exists():
        try:
            with open(creds_path) as f:
                return json.load(f)
        except Exception:
            return None
    return None
