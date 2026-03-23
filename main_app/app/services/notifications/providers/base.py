"""
Abstract base class for notification providers.

All email/SMS/push providers must implement this interface.
"""
from abc import ABC, abstractmethod
from typing import Optional


class NotificationProvider(ABC):
    """Base class for all notification delivery providers."""

    @abstractmethod
    def send_email(
        self,
        to_email: str,
        subject: str,
        html_body: str,
        plain_body: Optional[str] = None,
        from_email: Optional[str] = None,
        reply_to: Optional[str] = None,
    ) -> dict:
        """Send a single email.

        Returns:
            dict with keys: status ('sent' | 'failed'), message_id (optional), error (optional)
        """
        ...

    def send_bulk_email(self, recipients: list[str], subject: str, html_body: str, **kwargs) -> dict:
        """Send the same email to multiple recipients. Default: loop over send_email."""
        results = []
        for email in recipients:
            results.append(self.send_email(email, subject, html_body, **kwargs))
        sent = sum(1 for r in results if r.get('status') == 'sent')
        return {'sent': sent, 'failed': len(recipients) - sent, 'results': results}
