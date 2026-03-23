"""
Gmail SMTP notification provider.

Uses SMTP with TLS (port 587) and Google App Passwords.
Environment variables:
    GMAIL_SMTP_USER      — the Gmail address used for SMTP login
    GMAIL_SMTP_PASSWORD  — a Google App Password (NOT the account password)
    GMAIL_SMTP_FROM      — the "From" email address (defaults to GMAIL_SMTP_USER)
    GMAIL_SMTP_FROM_NAME — optional display name (e.g. "AgentGenie")
"""
import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from .base import NotificationProvider

logger = logging.getLogger(__name__)

SMTP_HOST = 'smtp.gmail.com'
SMTP_PORT = 587


class GmailSMTPProvider(NotificationProvider):
    """Send email via Gmail SMTP with TLS."""

    def __init__(self):
        self.user = os.environ.get('GMAIL_SMTP_USER', '')
        self.password = os.environ.get('GMAIL_SMTP_PASSWORD', '')
        self.from_address = os.environ.get('GMAIL_SMTP_FROM', self.user)
        self.from_name = os.environ.get('GMAIL_SMTP_FROM_NAME', '')

    @property
    def default_from(self) -> str:
        """Return a formatted 'From' header value."""
        if self.from_name:
            return f"{self.from_name} <{self.from_address}>"
        return self.from_address

    def _is_configured(self) -> bool:
        return bool(self.user and self.password)

    def send_email(
        self,
        to_email: str,
        subject: str,
        html_body: str,
        plain_body: Optional[str] = None,
        from_email: Optional[str] = None,
        reply_to: Optional[str] = None,
    ) -> dict:
        if not self._is_configured():
            logger.warning('Gmail SMTP not configured — email not sent to %s', to_email)
            return {'status': 'failed', 'error': 'SMTP credentials not configured'}

        sender = from_email or self.default_from

        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = sender
        msg['To'] = to_email
        if reply_to:
            msg['Reply-To'] = reply_to

        if plain_body:
            msg.attach(MIMEText(plain_body, 'plain', 'utf-8'))
        msg.attach(MIMEText(html_body, 'html', 'utf-8'))

        try:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(self.user, self.password)
                server.sendmail(self.from_address, [to_email], msg.as_string())
            logger.info('Email sent to %s: %s', to_email, subject)
            return {'status': 'sent'}
        except smtplib.SMTPAuthenticationError as e:
            logger.error('SMTP auth failed: %s', e)
            return {'status': 'failed', 'error': f'Authentication failed: {e}'}
        except Exception as e:
            logger.exception('SMTP send error to %s: %s', to_email, e)
            return {'status': 'failed', 'error': str(e)}
