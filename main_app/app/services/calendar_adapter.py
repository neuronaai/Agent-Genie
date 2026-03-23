"""Google Calendar adapter — OAuth 2.0 flow, availability checks, and booking.

This is the primary calendar provider for v1.  The adapter abstraction is
designed so that additional providers (Cal.com, Outlook, etc.) can be added
later by implementing the same interface.

Provider interface:
    check_availability(credentials, date, time, duration_minutes) -> dict
    book_appointment(credentials, date, time, caller_name, caller_phone, notes, duration_minutes) -> dict
    send_invite(credentials, attendee_email, date, time, duration_minutes, summary) -> dict
    build_oauth_url(state) -> str
    exchange_code(code) -> dict
"""
import logging
from datetime import datetime, timedelta, timezone

from flask import current_app

logger = logging.getLogger(__name__)

PROVIDER_NAME = 'google_calendar'

# Google Calendar API scopes
SCOPES = [
    'https://www.googleapis.com/auth/calendar.readonly',
    'https://www.googleapis.com/auth/calendar.events',
]


# ---------------------------------------------------------------------------
# OAuth 2.0 Flow
# ---------------------------------------------------------------------------
def build_oauth_url(state: str) -> str:
    """Build the Google OAuth 2.0 authorization URL.

    ``state`` should encode the tenant_id and connection_id so the callback
    can route the tokens to the correct tenant.
    """
    from urllib.parse import urlencode

    client_id = current_app.config.get('GOOGLE_CLIENT_ID', '')
    redirect_uri = current_app.config.get('GOOGLE_REDIRECT_URI', '')

    params = {
        'client_id': client_id,
        'redirect_uri': redirect_uri,
        'response_type': 'code',
        'scope': ' '.join(SCOPES),
        'access_type': 'offline',       # ensures we get a refresh_token
        'prompt': 'consent',            # force consent to always get refresh_token
        'state': state,
    }
    return f'https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}'


def exchange_code(code: str) -> dict:
    """Exchange an authorization code for access + refresh tokens.

    Returns a credentials dict ready for encrypted storage, or an error dict.
    """
    import requests as http_requests

    client_id = current_app.config.get('GOOGLE_CLIENT_ID', '')
    client_secret = current_app.config.get('GOOGLE_CLIENT_SECRET', '')
    redirect_uri = current_app.config.get('GOOGLE_REDIRECT_URI', '')

    try:
        resp = http_requests.post('https://oauth2.googleapis.com/token', data={
            'client_id': client_id,
            'client_secret': client_secret,
            'code': code,
            'grant_type': 'authorization_code',
            'redirect_uri': redirect_uri,
        }, timeout=10)

        if resp.status_code != 200:
            logger.error(f'Google token exchange failed: {resp.status_code} {resp.text[:300]}')
            return {'status': 'error', 'message': f'Token exchange failed: {resp.status_code}'}

        data = resp.json()
        credentials = {
            'access_token': data['access_token'],
            'refresh_token': data.get('refresh_token', ''),
            'token_type': data.get('token_type', 'Bearer'),
            'expires_at': datetime.now(timezone.utc).timestamp() + data.get('expires_in', 3600),
            'scope': data.get('scope', ''),
        }
        return {'status': 'success', 'credentials': credentials}

    except Exception as e:
        logger.error(f'Google token exchange exception: {e}')
        return {'status': 'error', 'message': str(e)}


# ---------------------------------------------------------------------------
# Calendar API helpers
# ---------------------------------------------------------------------------
def _get_calendar_service(access_token: str):
    """Build a Google Calendar API service object from an access token."""
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials(token=access_token)
    return build('calendar', 'v3', credentials=creds, cache_discovery=False)


def _parse_datetime(date_str: str, time_str: str) -> datetime:
    """Parse date (YYYY-MM-DD) and time (HH:MM) strings into a UTC datetime."""
    dt_str = f'{date_str}T{time_str}:00'
    return datetime.fromisoformat(dt_str).replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Provider Interface
# ---------------------------------------------------------------------------
def check_availability(credentials: dict, date: str, time: str,
                       duration_minutes: int = 30) -> dict:
    """Check if a time slot is available on the primary calendar.

    Returns:
        {'status': 'ok', 'available': bool, 'message': str}
    """
    access_token = credentials.get('access_token', '')
    if not access_token:
        return {'status': 'error', 'message': 'No access token available.'}

    try:
        service = _get_calendar_service(access_token)
        start = _parse_datetime(date, time)
        end = start + timedelta(minutes=duration_minutes)

        body = {
            'timeMin': start.isoformat(),
            'timeMax': end.isoformat(),
            'items': [{'id': 'primary'}],
        }
        result = service.freebusy().query(body=body).execute()
        busy = result.get('calendars', {}).get('primary', {}).get('busy', [])

        available = len(busy) == 0
        msg = 'The requested time slot is available.' if available else 'That time slot is already booked.'
        return {'status': 'ok', 'available': available, 'message': msg, 'provider': PROVIDER_NAME}

    except Exception as e:
        logger.error(f'Google Calendar availability check failed: {e}')
        return {'status': 'error', 'message': str(e), 'provider': PROVIDER_NAME}


def book_appointment(credentials: dict, date: str, time: str,
                     caller_name: str, caller_phone: str = '',
                     notes: str = '', duration_minutes: int = 30) -> dict:
    """Book an appointment on the primary calendar.

    Returns:
        {'status': 'ok', 'confirmation_id': str, 'message': str}
    """
    access_token = credentials.get('access_token', '')
    if not access_token:
        return {'status': 'error', 'message': 'No access token available.'}

    try:
        service = _get_calendar_service(access_token)
        start = _parse_datetime(date, time)
        end = start + timedelta(minutes=duration_minutes)

        event = {
            'summary': f'Appointment — {caller_name}',
            'description': (
                f'Booked by AgentGenie AI Agent\n'
                f'Caller: {caller_name}\n'
                f'Phone: {caller_phone}\n'
                f'Notes: {notes}'
            ),
            'start': {'dateTime': start.isoformat(), 'timeZone': 'UTC'},
            'end': {'dateTime': end.isoformat(), 'timeZone': 'UTC'},
        }
        created = service.events().insert(calendarId='primary', body=event).execute()
        event_id = created.get('id', '')

        return {
            'status': 'ok',
            'confirmation_id': event_id,
            'html_link': created.get('htmlLink', ''),
            'message': f'Appointment booked for {caller_name} on {date} at {time}.',
            'provider': PROVIDER_NAME,
        }

    except Exception as e:
        logger.error(f'Google Calendar booking failed: {e}')
        return {'status': 'error', 'message': str(e), 'provider': PROVIDER_NAME}


def send_invite(credentials: dict, attendee_email: str, date: str, time: str,
                duration_minutes: int = 30, summary: str = 'Appointment') -> dict:
    """Create a calendar event with an attendee, triggering a Google invite email.

    Returns:
        {'status': 'ok', 'message': str}
    """
    access_token = credentials.get('access_token', '')
    if not access_token:
        return {'status': 'error', 'message': 'No access token available.'}

    try:
        service = _get_calendar_service(access_token)
        start = _parse_datetime(date, time)
        end = start + timedelta(minutes=duration_minutes)

        event = {
            'summary': summary,
            'description': 'Scheduled via AgentGenie AI Agent.',
            'start': {'dateTime': start.isoformat(), 'timeZone': 'UTC'},
            'end': {'dateTime': end.isoformat(), 'timeZone': 'UTC'},
            'attendees': [{'email': attendee_email}],
            'reminders': {'useDefault': True},
        }
        created = service.events().insert(
            calendarId='primary', body=event, sendUpdates='all'
        ).execute()

        return {
            'status': 'ok',
            'event_id': created.get('id', ''),
            'message': f'Calendar invite sent to {attendee_email}.',
            'provider': PROVIDER_NAME,
        }

    except Exception as e:
        logger.error(f'Google Calendar invite failed: {e}')
        return {'status': 'error', 'message': str(e), 'provider': PROVIDER_NAME}
