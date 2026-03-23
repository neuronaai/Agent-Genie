"""Credential manager — encrypted storage, auto-refresh, and tenant-scoped isolation.

All provider credentials (OAuth tokens, API keys) are encrypted at rest using
Fernet symmetric encryption.  The encryption key is stored in the environment
(CREDENTIAL_ENCRYPTION_KEY) and never persisted alongside the ciphertext.

Key design decisions:
  - Strict tenant-scoped isolation: every read/write requires an explicit tenant_id.
  - Automatic token refresh: callers use ``get_valid_credentials()`` which transparently
    refreshes expired OAuth tokens before returning them.
  - Reconnect flow: when a refresh fails (revoked token, etc.) the connection status
    is set to ``needs_reconnect`` so the UI can prompt the user.
"""
import json
import logging
from datetime import datetime, timezone

from cryptography.fernet import Fernet, InvalidToken
from flask import current_app

from app import db

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Encryption helpers
# ---------------------------------------------------------------------------
def _get_fernet() -> Fernet:
    """Return a Fernet instance using the app-level encryption key."""
    key = current_app.config.get('CREDENTIAL_ENCRYPTION_KEY', '')
    if not key:
        # In dev mode without a key, use a deterministic fallback (NOT for production)
        logger.warning('CREDENTIAL_ENCRYPTION_KEY not set — using dev fallback')
        key = Fernet.generate_key().decode()  # ephemeral per-process; tokens won't survive restart
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_credentials(data: dict) -> str:
    """Encrypt a credentials dict to a Fernet token string."""
    f = _get_fernet()
    plaintext = json.dumps(data).encode('utf-8')
    return f.encrypt(plaintext).decode('utf-8')


def decrypt_credentials(ciphertext: str) -> dict:
    """Decrypt a Fernet token string back to a credentials dict."""
    f = _get_fernet()
    try:
        plaintext = f.decrypt(ciphertext.encode('utf-8'))
        return json.loads(plaintext)
    except (InvalidToken, json.JSONDecodeError) as e:
        logger.error(f'Failed to decrypt credentials: {e}')
        return {}


# ---------------------------------------------------------------------------
# Tenant-scoped credential CRUD
# ---------------------------------------------------------------------------
def store_credentials(connection_id: str, tenant_id: str, credentials: dict) -> bool:
    """Encrypt and store credentials for a tenant tool connection.

    Enforces tenant isolation by verifying the connection belongs to the tenant.
    """
    from app.models.core import TenantToolConnection

    conn = db.session.get(TenantToolConnection, connection_id)
    if not conn or conn.tenant_id != tenant_id:
        logger.warning(f'Credential store denied: connection {connection_id} / tenant {tenant_id}')
        return False

    conn.credentials_encrypted = encrypt_credentials(credentials)
    conn.status = 'connected'
    conn.connected_at = datetime.now(timezone.utc)
    db.session.commit()
    return True


def get_credentials(connection_id: str, tenant_id: str) -> dict:
    """Retrieve and decrypt credentials for a tenant tool connection.

    Returns an empty dict if the connection does not belong to the tenant or
    decryption fails.
    """
    from app.models.core import TenantToolConnection

    conn = db.session.get(TenantToolConnection, connection_id)
    if not conn or conn.tenant_id != tenant_id:
        return {}
    if not conn.credentials_encrypted:
        return {}
    return decrypt_credentials(conn.credentials_encrypted)


def clear_credentials(connection_id: str, tenant_id: str) -> bool:
    """Clear credentials and mark the connection as disconnected."""
    from app.models.core import TenantToolConnection

    conn = db.session.get(TenantToolConnection, connection_id)
    if not conn or conn.tenant_id != tenant_id:
        return False

    conn.credentials_encrypted = None
    conn.status = 'disconnected'
    conn.connected_at = None
    db.session.commit()
    return True


# ---------------------------------------------------------------------------
# OAuth token refresh
# ---------------------------------------------------------------------------
def get_valid_credentials(connection_id: str, tenant_id: str, provider: str = 'google') -> dict:
    """Return valid (non-expired) credentials, refreshing if necessary.

    If the refresh fails, the connection is marked ``needs_reconnect`` and an
    empty dict is returned so the caller can handle gracefully.
    """
    creds = get_credentials(connection_id, tenant_id)
    if not creds:
        return {}

    # Check if this is an OAuth token that might need refreshing
    if 'refresh_token' not in creds:
        # API-key style credential — always valid
        return creds

    # Check expiry
    expires_at = creds.get('expires_at')
    if expires_at:
        from datetime import datetime as dt
        try:
            exp = dt.fromisoformat(expires_at) if isinstance(expires_at, str) else dt.fromtimestamp(expires_at, tz=timezone.utc)
            if exp > datetime.now(timezone.utc):
                return creds  # still valid
        except (ValueError, TypeError):
            pass  # treat as expired

    # Attempt refresh
    refreshed = _refresh_oauth_token(creds, provider)
    if refreshed:
        # Persist the refreshed credentials
        store_credentials(connection_id, tenant_id, refreshed)
        return refreshed

    # Refresh failed — mark for reconnect
    _mark_needs_reconnect(connection_id)
    return {}


def _refresh_oauth_token(creds: dict, provider: str) -> dict | None:
    """Attempt to refresh an OAuth token.  Returns updated creds or None."""
    if provider == 'google':
        return _refresh_google_token(creds)
    # Future providers can be added here
    return None


def _refresh_google_token(creds: dict) -> dict | None:
    """Refresh a Google OAuth2 access token using the stored refresh token."""
    import requests as http_requests

    refresh_token = creds.get('refresh_token')
    if not refresh_token:
        return None

    client_id = current_app.config.get('GOOGLE_CLIENT_ID', '')
    client_secret = current_app.config.get('GOOGLE_CLIENT_SECRET', '')

    if not client_id or not client_secret:
        logger.error('Google OAuth client credentials not configured')
        return None

    try:
        resp = http_requests.post('https://oauth2.googleapis.com/token', data={
            'client_id': client_id,
            'client_secret': client_secret,
            'refresh_token': refresh_token,
            'grant_type': 'refresh_token',
        }, timeout=10)

        if resp.status_code != 200:
            logger.error(f'Google token refresh failed: {resp.status_code} {resp.text[:200]}')
            return None

        data = resp.json()
        # Build updated credentials
        updated = {
            'access_token': data['access_token'],
            'refresh_token': refresh_token,  # Google doesn't always return a new one
            'token_type': data.get('token_type', 'Bearer'),
            'expires_at': (
                datetime.now(timezone.utc).timestamp() + data.get('expires_in', 3600)
            ),
            'scope': data.get('scope', creds.get('scope', '')),
        }
        # If Google returned a new refresh token, use it
        if 'refresh_token' in data:
            updated['refresh_token'] = data['refresh_token']

        return updated

    except Exception as e:
        logger.error(f'Google token refresh exception: {e}')
        return None


def _mark_needs_reconnect(connection_id: str):
    """Set connection status to needs_reconnect so the UI can prompt the user."""
    from app.models.core import TenantToolConnection

    conn = db.session.get(TenantToolConnection, connection_id)
    if conn:
        conn.status = 'needs_reconnect'
        db.session.commit()
        logger.info(f'Connection {connection_id} marked needs_reconnect')
