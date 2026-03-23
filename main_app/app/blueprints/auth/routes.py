from datetime import datetime, timezone

from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app
from flask_login import login_user, logout_user, login_required, current_user
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature
from werkzeug.security import generate_password_hash, check_password_hash

from app import db
from app.models.core import User, Tenant, Membership, Organization
from app.blueprints.auth.forms import SignupForm, LoginForm, ForgotPasswordForm, ResetPasswordForm

public_bp = Blueprint('public', __name__)


def get_serializer():
    return URLSafeTimedSerializer(current_app.config['SECRET_KEY'])


# ---- Landing & Pricing ----
@public_bp.route('/')
def landing():
    return render_template('public/landing.html')


@public_bp.route('/pricing')
def pricing():
    from app.models.core import PlanDefinition
    plans = db.session.query(PlanDefinition).filter_by(is_active=True).order_by(PlanDefinition.price_monthly_cents).all()
    return render_template('public/pricing.html', plans=plans)


# ---- Signup ----
@public_bp.route('/signup', methods=['GET', 'POST'])
def signup():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard.home'))

    form = SignupForm()
    if form.validate_on_submit():
        user = User(
            email=form.email.data.lower(),
            password_hash=generate_password_hash(form.password.data),
        )
        db.session.add(user)

        tenant = Tenant(type='direct')
        db.session.add(tenant)
        db.session.flush()

        membership = Membership(user_id=user.id, tenant_id=tenant.id, role='owner')
        db.session.add(membership)
        db.session.commit()

        # Send verification email (best-effort)
        try:
            _send_verification_email(user)
        except Exception:
            pass

        # Send welcome notification (best-effort)
        try:
            from app.services.notifications.dispatcher import notify
            notify(
                'welcome',
                to_email=user.email,
                tenant_id=tenant.id,
                context={
                    'name': user.email.split('@')[0],
                    'dashboard_url': url_for('dashboard.home', _external=True),
                },
            )
        except Exception:
            pass

        login_user(user)
        flash('Account created! Please check your email to verify.', 'success')
        return redirect(url_for('public.onboarding'))

    return render_template('auth/signup.html', form=form)


# ---- Login ----
@public_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard.home'))

    form = LoginForm()
    if form.validate_on_submit():
        user = db.session.query(User).filter_by(email=form.email.data.lower()).first()
        if user and check_password_hash(user.password_hash, form.password.data):
            user.last_login_at = datetime.now(timezone.utc)
            db.session.commit()
            login_user(user, remember=form.remember.data)
            next_page = request.args.get('next')
            return redirect(next_page or url_for('dashboard.home'))
        flash('Invalid email or password.', 'danger')

    return render_template('auth/login.html', form=form)


# ---- Logout ----
@public_bp.route('/logout', methods=['POST'])
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('public.login'))


# ---- Email Verification ----
@public_bp.route('/verify-email/<token>')
def verify_email(token):
    s = get_serializer()
    try:
        data = s.loads(token, salt='email-verify', max_age=current_app.config['EMAIL_VERIFY_EXPIRY_SECONDS'])
    except (SignatureExpired, BadSignature):
        flash('Verification link is invalid or has expired.', 'danger')
        return redirect(url_for('public.login'))

    user = db.session.get(User, data['user_id'])
    if user:
        user.is_verified = True
        db.session.commit()
        flash('Email verified successfully!', 'success')
    return redirect(url_for('public.login'))


# ---- Forgot Password ----
@public_bp.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    form = ForgotPasswordForm()
    if form.validate_on_submit():
        user = db.session.query(User).filter_by(email=form.email.data.lower()).first()
        if user:
            try:
                _send_password_reset_email(user)
            except Exception:
                pass
        # Always show success to prevent email enumeration
        flash('If an account exists with that email, a reset link has been sent.', 'info')
        return redirect(url_for('public.login'))

    return render_template('auth/forgot_password.html', form=form)


# ---- Reset Password ----
@public_bp.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    s = get_serializer()
    try:
        data = s.loads(token, salt='password-reset', max_age=current_app.config['PASSWORD_RESET_EXPIRY_SECONDS'])
    except (SignatureExpired, BadSignature):
        flash('Reset link is invalid or has expired.', 'danger')
        return redirect(url_for('public.forgot_password'))

    form = ResetPasswordForm()
    if form.validate_on_submit():
        user = db.session.get(User, data['user_id'])
        if user:
            user.password_hash = generate_password_hash(form.password.data)
            db.session.commit()
            flash('Password has been reset. Please log in.', 'success')
            return redirect(url_for('public.login'))

    return render_template('auth/reset_password.html', form=form, token=token)


# ---- Onboarding ----
@public_bp.route('/onboarding', methods=['GET'])
@login_required
def onboarding():
    membership = db.session.query(Membership).filter_by(user_id=current_user.id).first()
    org = db.session.query(Organization).filter_by(tenant_id=membership.tenant_id).first() if membership else None
    if org:
        return redirect(url_for('dashboard.home'))
    return render_template('onboarding/step1.html')


@public_bp.route('/onboarding/organization', methods=['POST'])
@login_required
def onboarding_organization():
    membership = db.session.query(Membership).filter_by(user_id=current_user.id).first()
    if not membership:
        flash('No tenant found.', 'danger')
        return redirect(url_for('public.signup'))

    org_name = request.form.get('org_name', '').strip()
    industry = request.form.get('industry', '').strip()

    if not org_name:
        flash('Organization name is required.', 'danger')
        return redirect(url_for('public.onboarding'))

    org = Organization(
        tenant_id=membership.tenant_id,
        name=org_name,
        industry=industry or None,
    )
    db.session.add(org)
    db.session.commit()

    flash('Organization created!', 'success')
    return redirect(url_for('public.onboarding_plan'))


@public_bp.route('/onboarding/plan', methods=['GET'])
@login_required
def onboarding_plan():
    from app.models.core import PlanDefinition
    plans = db.session.query(PlanDefinition).filter_by(is_active=True).order_by(PlanDefinition.price_monthly_cents).all()
    return render_template('onboarding/step2.html', plans=plans)


@public_bp.route('/onboarding/plan', methods=['POST'])
@login_required
def onboarding_plan_select():
    from app.models.core import PlanDefinition, Subscription
    plan_id = request.form.get('plan_id')
    plan = db.session.get(PlanDefinition, plan_id)
    if not plan:
        flash('Invalid plan selected.', 'danger')
        return redirect(url_for('public.onboarding_plan'))

    membership = db.session.query(Membership).filter_by(user_id=current_user.id).first()

    # Create local subscription record (Stripe integration in Phase 4)
    sub = Subscription(
        tenant_id=membership.tenant_id,
        plan_id=plan.id,
        status='active',
    )
    db.session.add(sub)
    db.session.commit()

    # Send plan purchased notification (best-effort)
    try:
        from app.services.notifications.dispatcher import notify
        notify(
            'plan_purchased',
            to_email=current_user.email,
            tenant_id=membership.tenant_id,
            context={
                'plan_name': plan.name,
                'included_minutes': plan.included_minutes,
                'included_numbers': plan.included_numbers,
                'dashboard_url': url_for('dashboard.home', _external=True),
            },
        )
    except Exception:
        pass

    flash(f'Subscribed to {plan.name} plan!', 'success')
    return redirect(url_for('dashboard.home'))


# ---- Helper Functions ----
def _send_verification_email(user):
    from app.services.notifications.dispatcher import notify
    s = get_serializer()
    token = s.dumps({'user_id': user.id}, salt='email-verify')
    verify_url = url_for('public.verify_email', token=token, _external=True)
    current_app.logger.info(f'Verification URL for {user.email}: {verify_url}')
    notify(
        'email_verification',
        to_email=user.email,
        context={'verification_url': verify_url},
        send_in_app=False,
    )


def _send_password_reset_email(user):
    from app.services.notifications.dispatcher import notify
    s = get_serializer()
    token = s.dumps({'user_id': user.id}, salt='password-reset')
    reset_url = url_for('public.reset_password', token=token, _external=True)
    current_app.logger.info(f'Password reset URL for {user.email}: {reset_url}')
    notify(
        'password_reset',
        to_email=user.email,
        context={'reset_url': reset_url},
        send_in_app=False,
    )
