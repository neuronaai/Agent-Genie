import os
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect
from flask_mail import Mail
from flask_migrate import Migrate

db = SQLAlchemy()
login_manager = LoginManager()
csrf = CSRFProtect()
mail = Mail()
migrate = Migrate()

login_manager.login_view = 'public.login'
login_manager.login_message_category = 'info'


def create_app(config_name=None):
    """Application factory."""
    app = Flask(
        __name__,
        template_folder=os.path.join(os.path.dirname(__file__), 'templates'),
        static_folder=os.path.join(os.path.dirname(__file__), 'static'),
    )

    if config_name is None:
        config_name = os.environ.get('FLASK_ENV', 'development')

    from config import config_map
    app.config.from_object(config_map.get(config_name, config_map['development']))

    # Initialize extensions
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)
    mail.init_app(app)

    # Import models so they are registered with SQLAlchemy
    from app.models import core  # noqa: F401

    # Register blueprints
    from app.blueprints.auth.routes import public_bp
    from app.blueprints.dashboard.routes import dashboard_bp
    from app.blueprints.partner.routes import partner_bp
    from app.blueprints.admin.routes import admin_bp
    from app.blueprints.api.routes import api_bp

    app.register_blueprint(public_bp)
    app.register_blueprint(dashboard_bp, url_prefix='/app')
    app.register_blueprint(partner_bp, url_prefix='/partner')
    app.register_blueprint(admin_bp, url_prefix='/admin')
    app.register_blueprint(api_bp, url_prefix='/api')

    # Register tenant resolution middleware
    from app.services.tenant.middleware import register_tenant_middleware
    register_tenant_middleware(app)

    # User loader for Flask-Login
    @login_manager.user_loader
    def load_user(user_id):
        from app.models.core import User
        return db.session.get(User, user_id)

    # ── Error handlers ──
    @app.errorhandler(413)
    def request_entity_too_large(error):
        """Handle uploads that exceed MAX_CONTENT_LENGTH."""
        from flask import flash, redirect, request as req
        max_mb = app.config.get('MAX_CONTENT_LENGTH', 0) // (1024 * 1024)
        flash(f'Upload too large. Maximum allowed size is {max_mb} MB.', 'error')
        return redirect(req.referrer or '/app'), 302

    return app
