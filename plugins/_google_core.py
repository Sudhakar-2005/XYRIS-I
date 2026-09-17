"""
Shared Google OAuth helper for Gmail / Calendar plugins.

This file is prefixed with '_' so the plugin loader skips it — it is
imported by other plugin files, never loaded as a standalone tool.

Setup:
  1. Create a Google Cloud project → https://console.cloud.google.com
  2. Enable the Gmail API (and Calendar API if you want it later).
  3. Create an OAuth 2.0 Desktop App credential → download the JSON.
  4. Save it as  plugins/client_secret.json  (next to this file).
  5. On first use the plugin opens a browser for consent; the token is
     saved to  plugins/token_gmail.json  and reused thereafter.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

_PLUGINS_DIR = Path(__file__).resolve().parent
_CLIENT_SECRET = _PLUGINS_DIR / "client_secret.json"
_TOKEN_DIR = _PLUGINS_DIR


def _token_path(namespace: str = "gmail") -> Path:
    return _TOKEN_DIR / f"token_{namespace}.json"


def credentials_valid(namespace: str = "gmail") -> bool:
    """True if we have a stored refresh token we can use."""
    tp = _token_path(namespace)
    if not tp.exists():
        return False
    try:
        data = json.loads(tp.read_text(encoding="utf-8"))
        return bool(data.get("refresh_token"))
    except Exception:
        return False


def get_service(api_name: str, api_version: str, scopes: list[str],
               namespace: str = "gmail"):
    """Build and return a Google API service object.

    Opens a browser for OAuth consent on first run. Subsequent calls use the
    stored refresh token. Returns None and prints an error if setup is
    incomplete or anything goes wrong.
    """
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
    except ImportError as e:
        print(f"[google_core] Missing package: {e}. "
              "Run: pip install google-api-python-client google-auth-oauthlib")
        return None

    tp = _token_path(namespace)
    creds = None

    # Try loading stored token
    if tp.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(tp), scopes)
        except Exception:
            creds = None

    # Refresh or do full OAuth flow
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception:
            creds = None

    if not creds or not creds.valid:
        if not _CLIENT_SECRET.exists():
            print(f"[google_core] Missing {_CLIENT_SECRET.name}. "
                  "Download your OAuth client credentials from Google Cloud "
                  "Console and save them there.")
            return None

        try:
            from google_auth_oauthlib.flow import InstalledAppFlow
        except ImportError as e:
            print(f"[google_core] Missing package: {e}. "
                  "Run: pip install google-auth-oauthlib")
            return None

        flow = InstalledAppFlow.from_client_secrets_file(
            str(_CLIENT_SECRET), scopes
        )
        # Try local server first; fall back to console for headless
        try:
            creds = flow.run_local_server(port=0, open_browser=True)
        except Exception:
            creds = flow.run_console()

        # Persist for next time
        try:
            tp.write_text(creds.to_json(), encoding="utf-8")
            print(f"[google_core] Token saved to {tp.name}")
        except Exception as e:
            print(f"[google_core] Warning: could not save token: {e}")

    try:
        return build(api_name, api_version, credentials=creds)
    except Exception as e:
        print(f"[google_core] Failed to build {api_name} service: {e}")
        return None


def revoke_token(namespace: str = "gmail") -> str:
    """Delete the stored token file. Returns a human-readable result."""
    tp = _token_path(namespace)
    if tp.exists():
        tp.unlink()
        return f"Token revoked ({tp.name} deleted)."
    return "No token found to revoke."
