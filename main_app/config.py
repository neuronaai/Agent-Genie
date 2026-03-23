import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    """Base configuration."""
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # Render provides DATABASE_URL with 'postgres://' prefix;
    # SQLAlchemy 2.x requires 'postgresql://'.
    _raw_db_url = os.environ.get('DATABASE_URL', 'sqlite:///dev.db')
    if _raw_db_url.startswith('postgres://'):
        _raw_db_url = _raw_db_url.replace('postgres://', 'postgresql://', 1)
    SQLALCHEMY_DATABASE_URI = _raw_db_url

    # Flask-Mail
    MAIL_SERVER = os.environ.get('MAIL_SERVER', 'smtp.gmail.com')
    MAIL_PORT = int(os.environ.get('MAIL_PORT', 587))
    MAIL_USE_TLS = True
    MAIL_USERNAME = os.environ.get('MAIL_USERNAME')
    MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD')
    MAIL_DEFAULT_SENDER = os.environ.get('MAIL_DEFAULT_SENDER', 'noreply@platform.com')

    # Platform
    PLATFORM_NAME = os.environ.get('PLATFORM_NAME', 'AgentGenie')
    PLATFORM_DOMAIN = os.environ.get('PLATFORM_DOMAIN', 'localhost:5000')

    # Upload size limit — enforced globally by Flask/Werkzeug before any
    # route code runs.  Requests exceeding this limit receive a 413 error.
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16 MB (allows 10 MB files + form overhead)

    # Security
    PASSWORD_RESET_EXPIRY_SECONDS = 3600  # 1 hour
    EMAIL_VERIFY_EXPIRY_SECONDS = 86400   # 24 hours

    # Credential encryption key (Fernet) — generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    CREDENTIAL_ENCRYPTION_KEY = os.environ.get('CREDENTIAL_ENCRYPTION_KEY', '9kY10Vl6Y5YWgiP2hXxSne9XOXvDmJwY0IjEWJp_v9M=')

    # Stripe (Phase 4)
    STRIPE_SECRET_KEY = os.environ.get('STRIPE_SECRET_KEY')
    STRIPE_WEBHOOK_SECRET = os.environ.get('STRIPE_WEBHOOK_SECRET')

    # Retell AI
    RETELL_API_KEY = os.environ.get('RETELL_API_KEY',
                                     os.environ.get('RETELL_API_KEY_CUSTOM', ''))
    RETELL_WEBHOOK_SECRET = os.environ.get('RETELL_WEBHOOK_SECRET',
                                            os.environ.get('RETELL_WEBHOOK_SECRET_CUSTOM', ''))

    # OpenAI — for the agent config microservice
    OPENAI_API_KEY_CUSTOM = os.environ.get('OPENAI_API_KEY_CUSTOM', '')

    # -------------------------------------------------------------------------
    # Phase 8: Live Provider Integrations
    # -------------------------------------------------------------------------

    # Google Calendar (OAuth 2.0)
    GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID', '')
    GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET', '')
    GOOGLE_REDIRECT_URI = os.environ.get(
        'GOOGLE_REDIRECT_URI',
        'http://localhost:5000/app/integrations/google-calendar/callback'
    )

    # SendGrid (API key auth)
    SENDGRID_API_KEY = os.environ.get('SENDGRID_API_KEY', '')
    SENDGRID_FROM_EMAIL = os.environ.get('SENDGRID_FROM_EMAIL', 'noreply@agentgenie.ai')
    SENDGRID_FROM_NAME = os.environ.get('SENDGRID_FROM_NAME', 'AgentGenie')

    # Twilio (API key auth)
    TWILIO_ACCOUNT_SID = os.environ.get('TWILIO_ACCOUNT_SID', '')
    TWILIO_AUTH_TOKEN = os.environ.get('TWILIO_AUTH_TOKEN', '')
    TWILIO_PHONE_NUMBER = os.environ.get('TWILIO_PHONE_NUMBER', '')

    # Feature Flags
    FEATURE_PARTNER_PROGRAM = os.environ.get('FEATURE_PARTNER_PROGRAM', 'false').lower() in ('true', '1', 'yes')
    FEATURE_DFY = os.environ.get('FEATURE_DFY', 'false').lower() in ('true', '1', 'yes')
    FEATURE_CAMPAIGNS = os.environ.get('FEATURE_CAMPAIGNS', 'false').lower() in ('true', '1', 'yes')

    # OpenAI Brain Microservice
    OPENAI_BRAIN_URL = os.environ.get('OPENAI_BRAIN_URL', 'http://localhost:8100')

    # Notification Email Provider
    NOTIFICATION_EMAIL_PROVIDER = os.environ.get('NOTIFICATION_EMAIL_PROVIDER', 'gmail_smtp')

    # Celery / Redis
    CELERY_BROKER_URL = os.environ.get('CELERY_BROKER_URL', 'redis://localhost:6379/0')
    CELERY_RESULT_BACKEND = os.environ.get('CELERY_RESULT_BACKEND', 'redis://localhost:6379/0')


class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG = False


class TestingConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = 'sqlite:///test.db'


config_map = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
}
