"""Seed script to create initial data: plans, top-up packs, partner defaults, superadmin user, platform settings."""
from werkzeug.security import generate_password_hash
from app import create_app, db
from app.models.core import (
    PlanDefinition, PlatformSetting, User, Tenant, Membership
)


def seed():
    app = create_app()
    with app.app_context():
        # NOTE: Tables must already exist via 'flask db upgrade'.
        # Do NOT use db.create_all() — migrations are the single source of truth.

        # ── Seed plans (exact values from instructions) ──
        if db.session.query(PlanDefinition).count() == 0:
            plans = [
                PlanDefinition(
                    name='Starter',
                    price_monthly_cents=9900,        # $99/month
                    included_minutes=250,
                    included_agents=1,
                    included_numbers=1,
                    overage_rate_cents=39,            # $0.39/min
                    additional_number_rate_cents=900, # $9/month each
                ),
                PlanDefinition(
                    name='Growth',
                    price_monthly_cents=24900,       # $249/month
                    included_minutes=800,
                    included_agents=3,
                    included_numbers=3,
                    overage_rate_cents=35,            # $0.35/min
                    additional_number_rate_cents=800, # $8/month each
                ),
                PlanDefinition(
                    name='Scale',
                    price_monthly_cents=49900,       # $499/month
                    included_minutes=1800,
                    included_agents=8,
                    included_numbers=8,
                    overage_rate_cents=32,            # $0.32/min
                    additional_number_rate_cents=700, # $7/month each
                ),
            ]
            db.session.add_all(plans)
            print('Seeded 3 plans: Starter ($99), Growth ($249), Scale ($499).')

        # ── Seed superadmin if none exists ──
        admin_email = 'admin@platform.com'
        if not db.session.query(User).filter_by(email=admin_email).first():
            admin_user = User(
                email=admin_email,
                password_hash=generate_password_hash('admin123'),
                is_verified=True,
            )
            db.session.add(admin_user)
            admin_tenant = Tenant(type='direct')
            db.session.add(admin_tenant)
            db.session.flush()
            admin_membership = Membership(
                user_id=admin_user.id,
                tenant_id=admin_tenant.id,
                role='superadmin',
            )
            db.session.add(admin_membership)
            print(f'Seeded superadmin user: {admin_email} / admin123')

        # ── Seed platform settings ──
        defaults = {
            'settlement_hold_days': {
                'value': 30,
                'description': 'Days before revenue becomes eligible for partner payout',
            },
            'minimum_payout_cents': {
                'value': 5000,
                'description': 'Minimum payout threshold in cents ($50)',
            },
            'default_revenue_split_pct': {
                'value': 50,
                'description': 'Default partner revenue share percentage',
            },
            'recording_retention_days': {
                'value': 90,
                'description': 'Days to retain call recordings',
            },
            'recordings_enabled': {
                'value': True,
                'description': 'Global toggle: whether call recordings are visible to tenants',
            },
            'partner_setup_fee_cents': {
                'value': 49900,
                'description': 'One-time partner setup fee ($499)',
            },
            'partner_recurring_fee_cents': {
                'value': 9900,
                'description': 'Monthly partner platform fee ($99)',
            },
            'topup_100_min_cents': {
                'value': 3900,
                'description': '100-minute top-up pack price ($39)',
            },
            'topup_500_min_cents': {
                'value': 17500,
                'description': '500-minute top-up pack price ($175)',
            },
            'topup_1000_min_cents': {
                'value': 32000,
                'description': '1000-minute top-up pack price ($320)',
            },
        }
        for key, data in defaults.items():
            if not db.session.query(PlatformSetting).filter_by(key=key).first():
                db.session.add(PlatformSetting(
                    key=key, value=data['value'], description=data['description']
                ))

        db.session.commit()
        print('Seed complete.')


if __name__ == '__main__':
    seed()
