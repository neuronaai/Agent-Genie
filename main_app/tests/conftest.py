"""Shared pytest fixtures for AgentGenie test suite."""
import os
import sys
import pytest

# Ensure the app is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

os.environ.setdefault('FLASK_ENV', 'testing')
os.environ.setdefault('CREDENTIAL_ENCRYPTION_KEY', 'dGVzdC1rZXktZm9yLXVuaXQtdGVzdHMtMzItYnl0ZXM=')

from app import create_app, db as _db
from app.models.core import (
    Tenant, User, Organization, Membership, Agent, AgentConfig,
    PlanDefinition, TopupPackDefinition, Subscription, HandoffRule,
    GuardrailRule, WebhookEvent,
)
from werkzeug.security import generate_password_hash


@pytest.fixture(scope='session')
def app():
    """Create the Flask application for the test session."""
    application = create_app()
    application.config.update({
        'TESTING': True,
        'SQLALCHEMY_DATABASE_URI': 'sqlite:///test_remediation.db',
        'WTF_CSRF_ENABLED': False,
        'SERVER_NAME': 'localhost',
        'FEATURE_PARTNER_PROGRAM': False,
        'FEATURE_DFY': False,
        'FEATURE_CAMPAIGNS': False,
    })
    yield application


@pytest.fixture(scope='function')
def db(app):
    """Provide a clean database for each test."""
    with app.app_context():
        _db.create_all()
        yield _db
        _db.session.remove()
        _db.drop_all()


@pytest.fixture
def client(app, db):
    """Provide a test client."""
    return app.test_client()


@pytest.fixture
def seed_tenants(db):
    """Create two tenants for isolation testing."""
    t1 = Tenant(type='direct', status='active')
    t2 = Tenant(type='direct', status='active')
    db.session.add_all([t1, t2])
    db.session.flush()

    org1 = Organization(name='Tenant One', tenant_id=t1.id)
    org2 = Organization(name='Tenant Two', tenant_id=t2.id)
    db.session.add_all([org1, org2])

    u1 = User(email='user1@test.com', password_hash=generate_password_hash('pass1'))
    u2 = User(email='user2@test.com', password_hash=generate_password_hash('pass2'))
    db.session.add_all([u1, u2])
    db.session.flush()

    m1 = Membership(user_id=u1.id, tenant_id=t1.id, role='owner')
    m2 = Membership(user_id=u2.id, tenant_id=t2.id, role='owner')
    db.session.add_all([m1, m2])

    # Agents for each tenant
    a1 = Agent(tenant_id=t1.id, name='Agent T1', status='active')
    a2 = Agent(tenant_id=t2.id, name='Agent T2', status='active')
    db.session.add_all([a1, a2])
    db.session.flush()

    db.session.commit()
    return {
        'tenant1': t1, 'tenant2': t2,
        'user1': u1, 'user2': u2,
        'membership1': m1, 'membership2': m2,
        'agent1': a1, 'agent2': a2,
    }


@pytest.fixture
def superadmin(db):
    """Create a superadmin user with tenant."""
    tenant = Tenant(type='platform', status='active')
    db.session.add(tenant)
    db.session.flush()

    user = User(
        email='admin@platform.com',
        password_hash=generate_password_hash('admin123'),
        is_verified=True,
    )
    db.session.add(user)
    db.session.flush()

    membership = Membership(user_id=user.id, tenant_id=tenant.id, role='superadmin')
    db.session.add(membership)
    db.session.commit()
    return {'user': user, 'tenant': tenant, 'membership': membership}


@pytest.fixture
def seed_plans(db):
    """Seed plan definitions for testing."""
    plans = [
        PlanDefinition(name='Starter', price_monthly_cents=9900, included_minutes=250,
                        included_agents=1, included_numbers=1, overage_rate_cents=39, is_active=True),
        PlanDefinition(name='Growth', price_monthly_cents=24900, included_minutes=800,
                        included_agents=3, included_numbers=3, overage_rate_cents=35, is_active=True),
        PlanDefinition(name='Scale', price_monthly_cents=49900, included_minutes=1800,
                        included_agents=8, included_numbers=8, overage_rate_cents=32, is_active=True),
    ]
    db.session.add_all(plans)
    db.session.commit()
    return plans


@pytest.fixture
def seed_topups(db):
    """Seed top-up pack definitions for testing."""
    packs = [
        TopupPackDefinition(label='100 Minute Pack', minutes=100, price_cents=3900, is_active=True),
        TopupPackDefinition(label='500 Minute Pack', minutes=500, price_cents=17500, is_active=True),
        TopupPackDefinition(label='1000 Minute Pack', minutes=1000, price_cents=32000, is_active=True),
    ]
    db.session.add_all(packs)
    db.session.commit()
    return packs
