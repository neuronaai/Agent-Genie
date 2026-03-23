"""Tenant scoping utilities.

Provides helpers to enforce tenant isolation at the application layer.
Every query on a tenant-owned table MUST use these helpers.
"""
from functools import wraps

from flask import g, abort
from flask_login import current_user

from app import db


def get_current_tenant_id():
    """Return the current tenant_id from Flask g context, or abort 403."""
    tenant_id = getattr(g, 'tenant_id', None)
    if not tenant_id:
        abort(403, description='No tenant context found.')
    return tenant_id


def get_active_membership():
    """Return the current user's active Membership object, or abort 403.

    This is the canonical way to access the resolved membership anywhere
    in the request lifecycle.
    """
    membership = getattr(g, 'membership', None)
    if not membership:
        abort(403, description='No active membership found.')
    return membership


def scoped_query(model_class):
    """Return a SQLAlchemy query pre-filtered by the current tenant_id.

    Usage:
        agents = scoped_query(Agent).filter_by(status='active').all()
    """
    tenant_id = get_current_tenant_id()
    return db.session.query(model_class).filter(model_class.tenant_id == tenant_id)


def scoped_get_or_404(model_class, record_id):
    """Fetch a single record by PK and verify it belongs to the current tenant.

    Works for any model that has a ``tenant_id`` column.  Returns the
    record or aborts with 404.
    """
    tenant_id = get_current_tenant_id()
    record = db.session.get(model_class, record_id)
    if not record:
        abort(404)
    if hasattr(record, 'tenant_id') and str(record.tenant_id) != str(tenant_id):
        abort(404)
    return record


def require_role(*roles):
    """Decorator that checks the current user's membership role.

    Usage:
        @require_role('owner', 'admin')
        def some_view():
            ...
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            membership = getattr(g, 'membership', None)
            if not membership or membership.role not in roles:
                abort(403, description='Insufficient permissions.')
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def require_superadmin(f):
    """Decorator that restricts access to superadmin users only."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        membership = getattr(g, 'membership', None)
        if not membership or membership.role != 'superadmin':
            abort(403, description='Superadmin access required.')
        return f(*args, **kwargs)
    return decorated_function


def require_partner(f):
    """Decorator that restricts access to partner users only."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        membership = getattr(g, 'membership', None)
        if not membership or membership.role != 'partner':
            abort(403, description='Partner access required.')
        return f(*args, **kwargs)
    return decorated_function
