"""
Campaign Engine — orchestrates outbound calling campaigns.

Responsibilities:
- CSV import with validation and deduplication
- Global suppression list enforcement
- Campaign compilation (Contact → CampaignTask)
- Retell Batch Call dispatch
- Disposition mapping and retry eligibility
- Campaign stats rollup
"""
import csv
import io
import logging
import re
from datetime import datetime, timezone

from app import db
from app.models.core import (
    Contact, ContactList, Campaign, CampaignTask, CallLog, PhoneNumber,
    Agent,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Phone number validation
# ---------------------------------------------------------------------------
E164_PATTERN = re.compile(r'^\+[1-9]\d{6,14}$')

# US area code → timezone mapping (simplified; production would use libphonenumber)
US_AREA_CODE_TZ = {
    '201': 'America/New_York', '202': 'America/New_York', '203': 'America/New_York',
    '205': 'America/Chicago', '206': 'America/Los_Angeles', '207': 'America/New_York',
    '208': 'America/Boise', '209': 'America/Los_Angeles', '210': 'America/Chicago',
    '212': 'America/New_York', '213': 'America/Los_Angeles', '214': 'America/Chicago',
    '215': 'America/New_York', '216': 'America/New_York', '217': 'America/Chicago',
    '218': 'America/Chicago', '219': 'America/Chicago', '224': 'America/Chicago',
    '225': 'America/Chicago', '228': 'America/Chicago', '229': 'America/New_York',
    '231': 'America/New_York', '234': 'America/New_York', '239': 'America/New_York',
    '240': 'America/New_York', '248': 'America/New_York', '251': 'America/Chicago',
    '252': 'America/New_York', '253': 'America/Los_Angeles', '254': 'America/Chicago',
    '256': 'America/Chicago', '260': 'America/New_York', '262': 'America/Chicago',
    '267': 'America/New_York', '269': 'America/New_York', '270': 'America/New_York',
    '276': 'America/New_York', '281': 'America/Chicago', '301': 'America/New_York',
    '302': 'America/New_York', '303': 'America/Denver', '304': 'America/New_York',
    '305': 'America/New_York', '307': 'America/Denver', '308': 'America/Chicago',
    '309': 'America/Chicago', '310': 'America/Los_Angeles', '312': 'America/Chicago',
    '313': 'America/New_York', '314': 'America/Chicago', '315': 'America/New_York',
    '316': 'America/Chicago', '317': 'America/New_York', '318': 'America/Chicago',
    '319': 'America/Chicago', '320': 'America/Chicago', '321': 'America/New_York',
    '323': 'America/Los_Angeles', '325': 'America/Chicago', '330': 'America/New_York',
    '334': 'America/Chicago', '336': 'America/New_York', '337': 'America/Chicago',
    '339': 'America/New_York', '340': 'America/Virgin',
    '347': 'America/New_York', '351': 'America/New_York', '352': 'America/New_York',
    '360': 'America/Los_Angeles', '361': 'America/Chicago', '385': 'America/Denver',
    '386': 'America/New_York', '401': 'America/New_York', '402': 'America/Chicago',
    '404': 'America/New_York', '405': 'America/Chicago', '406': 'America/Denver',
    '407': 'America/New_York', '408': 'America/Los_Angeles', '409': 'America/Chicago',
    '410': 'America/New_York', '412': 'America/New_York', '413': 'America/New_York',
    '414': 'America/Chicago', '415': 'America/Los_Angeles', '417': 'America/Chicago',
    '419': 'America/New_York', '423': 'America/New_York', '424': 'America/Los_Angeles',
    '425': 'America/Los_Angeles', '430': 'America/Chicago', '432': 'America/Chicago',
    '434': 'America/New_York', '435': 'America/Denver', '440': 'America/New_York',
    '442': 'America/Los_Angeles', '443': 'America/New_York', '469': 'America/Chicago',
    '470': 'America/New_York', '475': 'America/New_York', '478': 'America/New_York',
    '479': 'America/Chicago', '480': 'America/Phoenix', '484': 'America/New_York',
    '501': 'America/Chicago', '502': 'America/New_York', '503': 'America/Los_Angeles',
    '504': 'America/Chicago', '505': 'America/Denver', '507': 'America/Chicago',
    '508': 'America/New_York', '509': 'America/Los_Angeles', '510': 'America/Los_Angeles',
    '512': 'America/Chicago', '513': 'America/New_York', '515': 'America/Chicago',
    '516': 'America/New_York', '517': 'America/New_York', '518': 'America/New_York',
    '520': 'America/Phoenix', '530': 'America/Los_Angeles', '531': 'America/Chicago',
    '534': 'America/Chicago', '539': 'America/Chicago', '540': 'America/New_York',
    '541': 'America/Los_Angeles', '551': 'America/New_York', '559': 'America/Los_Angeles',
    '561': 'America/New_York', '562': 'America/Los_Angeles', '563': 'America/Chicago',
    '567': 'America/New_York', '570': 'America/New_York', '571': 'America/New_York',
    '573': 'America/Chicago', '574': 'America/New_York', '575': 'America/Denver',
    '580': 'America/Chicago', '585': 'America/New_York', '586': 'America/New_York',
    '601': 'America/Chicago', '602': 'America/Phoenix', '603': 'America/New_York',
    '605': 'America/Chicago', '606': 'America/New_York', '607': 'America/New_York',
    '608': 'America/Chicago', '609': 'America/New_York', '610': 'America/New_York',
    '612': 'America/Chicago', '614': 'America/New_York', '615': 'America/Chicago',
    '616': 'America/New_York', '617': 'America/New_York', '618': 'America/Chicago',
    '619': 'America/Los_Angeles', '620': 'America/Chicago', '623': 'America/Phoenix',
    '626': 'America/Los_Angeles', '628': 'America/Los_Angeles', '629': 'America/Chicago',
    '630': 'America/Chicago', '631': 'America/New_York', '636': 'America/Chicago',
    '641': 'America/Chicago', '646': 'America/New_York', '650': 'America/Los_Angeles',
    '651': 'America/Chicago', '657': 'America/Los_Angeles', '660': 'America/Chicago',
    '661': 'America/Los_Angeles', '662': 'America/Chicago', '667': 'America/New_York',
    '669': 'America/Los_Angeles', '678': 'America/New_York', '681': 'America/New_York',
    '682': 'America/Chicago', '701': 'America/Chicago', '702': 'America/Los_Angeles',
    '703': 'America/New_York', '704': 'America/New_York', '706': 'America/New_York',
    '707': 'America/Los_Angeles', '708': 'America/Chicago', '712': 'America/Chicago',
    '713': 'America/Chicago', '714': 'America/Los_Angeles', '715': 'America/Chicago',
    '716': 'America/New_York', '717': 'America/New_York', '718': 'America/New_York',
    '719': 'America/Denver', '720': 'America/Denver', '724': 'America/New_York',
    '725': 'America/Los_Angeles', '727': 'America/New_York', '731': 'America/Chicago',
    '732': 'America/New_York', '734': 'America/New_York', '737': 'America/Chicago',
    '740': 'America/New_York', '743': 'America/New_York', '747': 'America/Los_Angeles',
    '754': 'America/New_York', '757': 'America/New_York', '760': 'America/Los_Angeles',
    '762': 'America/New_York', '763': 'America/Chicago', '765': 'America/New_York',
    '769': 'America/Chicago', '770': 'America/New_York', '772': 'America/New_York',
    '773': 'America/Chicago', '774': 'America/New_York', '775': 'America/Los_Angeles',
    '779': 'America/Chicago', '781': 'America/New_York', '785': 'America/Chicago',
    '786': 'America/New_York', '801': 'America/Denver', '802': 'America/New_York',
    '803': 'America/New_York', '804': 'America/New_York', '805': 'America/Los_Angeles',
    '806': 'America/Chicago', '808': 'Pacific/Honolulu', '810': 'America/New_York',
    '812': 'America/New_York', '813': 'America/New_York', '814': 'America/New_York',
    '815': 'America/Chicago', '816': 'America/Chicago', '817': 'America/Chicago',
    '818': 'America/Los_Angeles', '828': 'America/New_York', '830': 'America/Chicago',
    '831': 'America/Los_Angeles', '832': 'America/Chicago', '843': 'America/New_York',
    '845': 'America/New_York', '847': 'America/Chicago', '848': 'America/New_York',
    '850': 'America/New_York', '856': 'America/New_York', '857': 'America/New_York',
    '858': 'America/Los_Angeles', '859': 'America/New_York', '860': 'America/New_York',
    '862': 'America/New_York', '863': 'America/New_York', '864': 'America/New_York',
    '865': 'America/New_York', '870': 'America/Chicago', '872': 'America/Chicago',
    '878': 'America/New_York', '901': 'America/Chicago', '903': 'America/Chicago',
    '904': 'America/New_York', '906': 'America/New_York', '907': 'America/Anchorage',
    '908': 'America/New_York', '909': 'America/Los_Angeles', '910': 'America/New_York',
    '912': 'America/New_York', '913': 'America/Chicago', '914': 'America/New_York',
    '915': 'America/Denver', '916': 'America/Los_Angeles', '917': 'America/New_York',
    '918': 'America/Chicago', '919': 'America/New_York', '920': 'America/Chicago',
    '925': 'America/Los_Angeles', '928': 'America/Phoenix', '929': 'America/New_York',
    '931': 'America/Chicago', '936': 'America/Chicago', '937': 'America/New_York',
    '938': 'America/Chicago', '940': 'America/Chicago', '941': 'America/New_York',
    '947': 'America/New_York', '949': 'America/Los_Angeles', '951': 'America/Los_Angeles',
    '952': 'America/Chicago', '954': 'America/New_York', '956': 'America/Chicago',
    '959': 'America/New_York', '970': 'America/Denver', '971': 'America/Los_Angeles',
    '972': 'America/Chicago', '973': 'America/New_York', '978': 'America/New_York',
    '979': 'America/Chicago', '980': 'America/New_York', '984': 'America/New_York',
    '985': 'America/Chicago',
}

DEFAULT_TIMEZONE = 'America/New_York'


def derive_timezone(phone_number: str) -> str:
    """Derive IANA timezone from a US phone number's area code."""
    if phone_number.startswith('+1') and len(phone_number) >= 5:
        area_code = phone_number[2:5]
        return US_AREA_CODE_TZ.get(area_code, DEFAULT_TIMEZONE)
    return DEFAULT_TIMEZONE


def normalize_phone(raw: str) -> str:
    """Normalize a phone number to E.164 format. Returns empty string on failure."""
    digits = re.sub(r'[^\d+]', '', raw.strip())
    if digits.startswith('+'):
        if E164_PATTERN.match(digits):
            return digits
        return ''
    if len(digits) == 10:
        candidate = f'+1{digits}'
        if E164_PATTERN.match(candidate):
            return candidate
    if len(digits) == 11 and digits.startswith('1'):
        candidate = f'+{digits}'
        if E164_PATTERN.match(candidate):
            return candidate
    return ''


# ---------------------------------------------------------------------------
# CSV Import
# ---------------------------------------------------------------------------
def import_csv(tenant_id: str, list_name: str, file_content: str, description: str = None):
    """
    Import contacts from CSV content into a new ContactList.

    Expected CSV columns: phone_number (required), first_name, last_name, email, timezone
    Any additional columns are stored in dynamic_data.

    Returns (contact_list, stats_dict).
    """
    reader = csv.DictReader(io.StringIO(file_content))
    if not reader.fieldnames:
        raise ValueError("CSV file is empty or has no headers.")

    # Normalize headers
    headers = [h.strip().lower().replace(' ', '_') for h in reader.fieldnames]
    if 'phone_number' not in headers and 'phone' not in headers:
        raise ValueError("CSV must contain a 'phone_number' or 'phone' column.")

    phone_col = 'phone_number' if 'phone_number' in headers else 'phone'
    known_cols = {'phone_number', 'phone', 'first_name', 'last_name', 'email', 'timezone'}

    # Create the contact list
    contact_list = ContactList(
        tenant_id=tenant_id,
        name=list_name,
        description=description,
    )
    db.session.add(contact_list)
    db.session.flush()  # Get the ID

    stats = {'total_rows': 0, 'imported': 0, 'duplicates': 0, 'invalid': 0, 'suppressed': 0}
    seen_phones = set()

    # Get the tenant-wide suppression list (all opted-out phone numbers)
    suppressed_numbers = get_suppression_list(tenant_id)

    for raw_row in reader:
        stats['total_rows'] += 1
        # Normalize keys
        row = {k.strip().lower().replace(' ', '_'): v.strip() for k, v in raw_row.items() if v}

        raw_phone = row.get(phone_col, '')
        phone = normalize_phone(raw_phone)
        if not phone:
            stats['invalid'] += 1
            continue

        # Check suppression
        if phone in suppressed_numbers:
            stats['suppressed'] += 1
            continue

        # Dedup within this list (upsert: last row wins)
        if phone in seen_phones:
            stats['duplicates'] += 1
            # Update existing contact
            existing = Contact.query.filter_by(
                contact_list_id=contact_list.id,
                phone_number=phone,
            ).first()
            if existing:
                existing.first_name = row.get('first_name', existing.first_name)
                existing.last_name = row.get('last_name', existing.last_name)
                existing.email = row.get('email', existing.email)
                dynamic = {k: v for k, v in row.items() if k not in known_cols and v}
                if dynamic:
                    existing.dynamic_data = dynamic
            continue

        seen_phones.add(phone)

        # Derive timezone
        tz = row.get('timezone') or derive_timezone(phone)

        # Collect dynamic data (extra columns)
        dynamic_data = {k: v for k, v in row.items() if k not in known_cols and v}

        contact = Contact(
            tenant_id=tenant_id,
            contact_list_id=contact_list.id,
            phone_number=phone,
            first_name=row.get('first_name'),
            last_name=row.get('last_name'),
            email=row.get('email'),
            timezone=tz,
            dynamic_data=dynamic_data or None,
            status='active',
        )
        db.session.add(contact)
        stats['imported'] += 1

    contact_list.contact_count = stats['imported']
    db.session.commit()

    return contact_list, stats


# ---------------------------------------------------------------------------
# Global Suppression List
# ---------------------------------------------------------------------------
def get_suppression_list(tenant_id: str) -> set:
    """Return a set of phone numbers that are opted out for this tenant."""
    opted_out = db.session.query(Contact.phone_number).filter(
        Contact.tenant_id == tenant_id,
        Contact.status == 'opted_out',
    ).distinct().all()
    return {row[0] for row in opted_out}


def suppress_number(tenant_id: str, phone_number: str):
    """Mark a phone number as opted out across all contact lists for this tenant."""
    contacts = Contact.query.filter_by(
        tenant_id=tenant_id,
        phone_number=phone_number,
    ).all()
    now = datetime.now(timezone.utc)
    for c in contacts:
        c.status = 'opted_out'
        c.opted_out_at = now
    db.session.commit()
    return len(contacts)


def is_suppressed(tenant_id: str, phone_number: str) -> bool:
    """Check if a phone number is on the tenant's suppression list."""
    return db.session.query(Contact.id).filter(
        Contact.tenant_id == tenant_id,
        Contact.phone_number == phone_number,
        Contact.status == 'opted_out',
    ).first() is not None


# ---------------------------------------------------------------------------
# Campaign Compilation
# ---------------------------------------------------------------------------
def compile_campaign(campaign: Campaign) -> list:
    """
    Create CampaignTask records for all eligible contacts in the campaign's list.
    Returns the list of tasks created.

    Filters out:
    - Contacts with status != 'active'
    - Contacts on the global suppression list
    """
    suppressed = get_suppression_list(campaign.tenant_id)

    eligible_contacts = Contact.query.filter(
        Contact.contact_list_id == campaign.contact_list_id,
        Contact.status == 'active',
    ).all()

    tasks = []
    skipped = 0
    for contact in eligible_contacts:
        if contact.phone_number in suppressed:
            skipped += 1
            continue

        task = CampaignTask(
            campaign_id=campaign.id,
            contact_id=contact.id,
            status='pending',
        )
        db.session.add(task)
        tasks.append(task)

    campaign.total_tasks = len(tasks)
    db.session.commit()

    logger.info(
        f"Campaign {campaign.id}: compiled {len(tasks)} tasks, "
        f"skipped {skipped} suppressed contacts"
    )
    return tasks


def build_retell_tasks(campaign: Campaign, tasks: list) -> list:
    """
    Build the Retell Batch Call API task payload from CampaignTask records.
    """
    retell_tasks = []
    for task in tasks:
        contact = task.contact
        entry = {
            "to_number": contact.phone_number,
            "metadata": {
                "campaign_id": campaign.id,
                "task_id": task.id,
                "contact_id": contact.id,
                "tenant_id": campaign.tenant_id,
            },
        }
        # Inject dynamic variables for LLM personalization
        dynamic_vars = {}
        if contact.first_name:
            dynamic_vars["contact_first_name"] = contact.first_name
        if contact.last_name:
            dynamic_vars["contact_last_name"] = contact.last_name
        if contact.email:
            dynamic_vars["contact_email"] = contact.email
        if contact.dynamic_data:
            dynamic_vars.update(contact.dynamic_data)
        if dynamic_vars:
            entry["retell_llm_dynamic_variables"] = dynamic_vars

        retell_tasks.append(entry)

    return retell_tasks


def build_call_time_window(campaign: Campaign) -> dict:
    """Build the Retell call_time_window object from campaign settings."""
    window = {
        "windows": [{
            "start": campaign.window_start_min or 540,
            "end": campaign.window_end_min or 1260,
        }],
    }
    if campaign.allowed_days:
        window["day"] = campaign.allowed_days
    return window


# ---------------------------------------------------------------------------
# Disposition Mapping
# ---------------------------------------------------------------------------
DISPOSITION_MAP = {
    # Completed — no retry
    'user_hangup': ('completed', False),
    'agent_hangup': ('completed', False),
    'call_transfer': ('completed', False),
    # Voicemail — no retry (message left)
    'voicemail_reached': ('voicemail', False),
    # No answer — retry eligible
    'dial_busy': ('no_answer', True),
    'dial_no_answer': ('no_answer', True),
    # Invalid — no retry, mark contact
    'dial_failed': ('invalid_number', False),
    'invalid_number': ('invalid_number', False),
    # Errors — retry eligible
    'machine_detected': ('no_answer', True),
    'error_llm_websocket_open': ('error', True),
    'error_llm_websocket_lost_connection': ('error', True),
    'error_llm_websocket_runtime': ('error', True),
    'error_llm_websocket_corrupt_payload': ('error', True),
    'error_frontend_corrupted_payload': ('error', True),
    'error_twilio': ('error', True),
    'error_no_audio_received': ('error', True),
    'error_asr': ('error', True),
    'error_retell': ('error', True),
    'error_unknown': ('error', True),
    'error_user_not_joined': ('error', True),
    'registered_call_timeout': ('error', True),
}


def map_disposition(disconnection_reason: str) -> tuple:
    """
    Map a Retell disconnection_reason to (disposition, retry_eligible).
    Returns ('error', True) for unknown reasons.
    """
    return DISPOSITION_MAP.get(disconnection_reason, ('error', True))


def process_outbound_webhook(call_data: dict, tenant_id: str):
    """
    Process an outbound call webhook event.
    Updates the CampaignTask, creates a CallLog, and handles retry logic.
    """
    metadata = call_data.get('metadata', {})
    task_id = metadata.get('task_id')
    campaign_id = metadata.get('campaign_id')

    if not task_id or not campaign_id:
        logger.warning("Outbound webhook missing task_id or campaign_id in metadata")
        return

    task = CampaignTask.query.get(task_id)
    if not task:
        logger.warning(f"CampaignTask {task_id} not found")
        return

    campaign = Campaign.query.get(campaign_id)
    if not campaign:
        logger.warning(f"Campaign {campaign_id} not found")
        return

    # Map disposition
    disconnection_reason = call_data.get('disconnection_reason', 'error_unknown')
    disposition, retry_eligible = map_disposition(disconnection_reason)

    # Create CallLog
    call_log = CallLog(
        tenant_id=tenant_id,
        retell_call_id=call_data.get('call_id', ''),
        agent_id=campaign.agent_id,
        from_number=call_data.get('from_number', ''),
        to_number=call_data.get('to_number', ''),
        direction='outbound',
        duration_seconds=call_data.get('duration_ms', 0) // 1000 if call_data.get('duration_ms') else 0,
        status=call_data.get('call_status', 'ended'),
        disconnection_reason=disconnection_reason,
        transcript=call_data.get('transcript', ''),
        summary=call_data.get('call_analysis', {}).get('call_summary') if call_data.get('call_analysis') else None,
        sentiment=call_data.get('call_analysis', {}).get('user_sentiment') if call_data.get('call_analysis') else None,
        recording_url=call_data.get('recording_url'),
        retell_cost=call_data.get('cost'),
    )
    db.session.add(call_log)
    db.session.flush()

    # Update task
    task.call_log_id = call_log.id
    task.disposition = disposition
    task.last_attempted_at = datetime.now(timezone.utc)

    if disposition == 'invalid_number':
        # Mark the contact as invalid
        contact = task.contact
        if contact:
            contact.status = 'invalid_number'

    # Check for opt-out intent in call analysis
    call_analysis = call_data.get('call_analysis', {})
    if call_analysis and call_analysis.get('opt_out_detected'):
        contact = task.contact
        if contact:
            suppress_number(tenant_id, contact.phone_number)
        task.disposition = 'opted_out'
        task.status = 'completed'
    elif retry_eligible and task.retry_count < campaign.max_retries:
        task.retry_count += 1
        task.status = 'pending'  # Will be picked up by next batch
        task.error_message = f"Retry {task.retry_count}/{campaign.max_retries}: {disconnection_reason}"
    elif retry_eligible:
        task.status = 'failed'
        task.error_message = f"Max retries exceeded: {disconnection_reason}"
        campaign.failed_tasks = (campaign.failed_tasks or 0) + 1
    else:
        task.status = 'completed'
        campaign.completed_tasks = (campaign.completed_tasks or 0) + 1

    # Check if campaign is complete
    pending_count = CampaignTask.query.filter(
        CampaignTask.campaign_id == campaign.id,
        CampaignTask.status.in_(['pending', 'queued', 'calling']),
    ).count()

    if pending_count == 0 and campaign.status == 'running':
        campaign.status = 'completed'
        campaign.completed_at = datetime.now(timezone.utc)

    db.session.commit()
    return task
