"""Partner blueprint — gated behind FEATURE_PARTNER_PROGRAM flag.

The partner program is deferred to a future phase.  While the flag is off,
all routes under /partner/ return 404 so no normal user can reach them.
"""
from flask import Blueprint, render_template, abort, current_app
from flask_login import login_required

partner_bp = Blueprint('partner', __name__)


@partner_bp.before_request
@login_required
def before_request():
    """Block all partner routes when the feature flag is disabled."""
    if not current_app.config.get('FEATURE_PARTNER_PROGRAM'):
        abort(404, description='Partner program is not available yet.')


@partner_bp.route('/')
def home():
    return render_template('partner/home.html')
