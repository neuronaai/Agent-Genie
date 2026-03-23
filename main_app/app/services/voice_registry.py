"""
Voice & Language Registry.

Provides:
- Supported language list with human-readable labels
- Voice listing (from Retell API with local cache)
- Default voice per language
- Voice-language combination validation
"""
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Supported Languages (from Retell Create Agent API)
# ---------------------------------------------------------------------------
SUPPORTED_LANGUAGES = {
    "en-US": "English (US)",
    "en-GB": "English (UK)",
    "en-AU": "English (Australia)",
    "en-IN": "English (India)",
    "en-NZ": "English (New Zealand)",
    "es-ES": "Spanish (Spain)",
    "es-419": "Spanish (Latin America)",
    "fr-FR": "French (France)",
    "fr-CA": "French (Canada)",
    "de-DE": "German",
    "it-IT": "Italian",
    "pt-BR": "Portuguese (Brazil)",
    "pt-PT": "Portuguese (Portugal)",
    "nl-NL": "Dutch",
    "nl-BE": "Dutch (Belgium)",
    "ja-JP": "Japanese",
    "ko-KR": "Korean",
    "zh-CN": "Chinese (Mandarin)",
    "yue-CN": "Chinese (Cantonese)",
    "hi-IN": "Hindi",
    "ru-RU": "Russian",
    "pl-PL": "Polish",
    "tr-TR": "Turkish",
    "vi-VN": "Vietnamese",
    "sv-SE": "Swedish",
    "da-DK": "Danish",
    "no-NO": "Norwegian",
    "fi-FI": "Finnish",
    "el-GR": "Greek",
    "cs-CZ": "Czech",
    "ro-RO": "Romanian",
    "hu-HU": "Hungarian",
    "sk-SK": "Slovak",
    "bg-BG": "Bulgarian",
    "uk-UA": "Ukrainian",
    "th-TH": "Thai",
    "id-ID": "Indonesian",
    "ms-MY": "Malay",
    "ar-SA": "Arabic",
    "he-IL": "Hebrew",
    "fa-IR": "Persian",
    "af-ZA": "Afrikaans",
    "ca-ES": "Catalan",
    "hr-HR": "Croatian",
    "sr-RS": "Serbian",
    "sl-SI": "Slovenian",
    "lt-LT": "Lithuanian",
    "lv-LV": "Latvian",
    "is-IS": "Icelandic",
    "sw-KE": "Swahili",
    "fil-PH": "Filipino",
    "ta-IN": "Tamil",
    "kn-IN": "Kannada",
    "mr-IN": "Marathi",
    "ur-IN": "Urdu",
    "ne-NP": "Nepali",
    "multi": "Multilingual (auto-detect)",
}

# ---------------------------------------------------------------------------
# Voice Providers & Multilingual Capability
# ---------------------------------------------------------------------------
# Providers whose voices support multilingual use (non-English languages).
# Platform voices are Retell's own optimized voices with auto-fallback.
MULTILINGUAL_PROVIDERS = {"elevenlabs", "cartesia", "minimax", "platform", "openai"}

# ElevenLabs voice models that support multilingual
MULTILINGUAL_VOICE_MODELS = {
    "eleven_multilingual_v2", "eleven_turbo_v2_5", "eleven_flash_v2_5", "eleven_v3",
}

# ---------------------------------------------------------------------------
# Default Voice per Language
# ---------------------------------------------------------------------------
# These are sensible defaults. The voice_id values use Retell's naming convention.
# Platform voices are preferred where available; otherwise ElevenLabs multilingual.
DEFAULT_VOICE_PER_LANGUAGE = {
    "en-US": "11labs-Adrian",
    "en-GB": "11labs-Adrian",
    "en-AU": "11labs-Adrian",
    "en-IN": "11labs-Adrian",
    "en-NZ": "11labs-Adrian",
    "es-ES": "11labs-Adrian",
    "es-419": "11labs-Adrian",
    "fr-FR": "11labs-Adrian",
    "fr-CA": "11labs-Adrian",
    "de-DE": "11labs-Adrian",
    "it-IT": "11labs-Adrian",
    "pt-BR": "11labs-Adrian",
    "pt-PT": "11labs-Adrian",
    "ja-JP": "11labs-Adrian",
    "ko-KR": "11labs-Adrian",
    "zh-CN": "11labs-Adrian",
    "hi-IN": "11labs-Adrian",
    "ru-RU": "11labs-Adrian",
    "multi": "11labs-Adrian",
}

# Fallback default for any language not explicitly listed
FALLBACK_DEFAULT_VOICE = "11labs-Adrian"

# ---------------------------------------------------------------------------
# In-Memory Voice Cache
# ---------------------------------------------------------------------------
_voice_cache = {
    "voices": [],
    "fetched_at": 0,
    "ttl": 300,  # 5-minute cache
}


def get_languages() -> list[dict]:
    """Return the supported language list as [{code, label}]."""
    return [{"code": code, "label": label} for code, label in SUPPORTED_LANGUAGES.items()]


def get_default_voice(language: str) -> str:
    """Return the default voice_id for a given language code."""
    return DEFAULT_VOICE_PER_LANGUAGE.get(language, FALLBACK_DEFAULT_VOICE)


def list_voices(force_refresh: bool = False) -> list[dict]:
    """
    Return the list of available voices.

    Fetches from Retell API and caches for 5 minutes.
    Each voice dict has: voice_id, voice_name, provider, gender, accent, age, preview_audio_url
    """
    now = time.time()
    if not force_refresh and _voice_cache["voices"] and (now - _voice_cache["fetched_at"] < _voice_cache["ttl"]):
        return _voice_cache["voices"]

    try:
        from app.services import retell_adapter
        import requests

        resp = requests.get(
            f"{retell_adapter.RETELL_BASE_URL}/list-voices",
            headers=retell_adapter._headers(),
            timeout=retell_adapter.DEFAULT_TIMEOUT,
        )
        if resp.status_code == 200:
            voices = resp.json()
            _voice_cache["voices"] = voices
            _voice_cache["fetched_at"] = now
            logger.info(f"Fetched {len(voices)} voices from Retell API")
            return voices
        else:
            logger.warning(f"Failed to fetch voices from Retell: {resp.status_code}")
    except Exception as e:
        logger.warning(f"Error fetching voices from Retell: {e}")

    # Return cached data even if stale, or fallback
    if _voice_cache["voices"]:
        return _voice_cache["voices"]

    # Absolute fallback: return a minimal set of known voices
    return _get_fallback_voices()


def _get_fallback_voices() -> list[dict]:
    """Fallback voice list when Retell API is unreachable."""
    return [
        {"voice_id": "11labs-Adrian", "voice_name": "Adrian", "provider": "elevenlabs", "gender": "male", "accent": "American", "age": "Young", "preview_audio_url": "https://retell-utils-public.s3.us-west-2.amazonaws.com/adrian.mp3"},
        {"voice_id": "11labs-Myra", "voice_name": "Myra", "provider": "elevenlabs", "gender": "female", "accent": "American", "age": "Young", "preview_audio_url": "https://retell-utils-public.s3.us-west-2.amazonaws.com/myra.mp3"},
        {"voice_id": "11labs-Paola", "voice_name": "Paola", "provider": "elevenlabs", "gender": "female", "accent": "American", "age": "Young", "preview_audio_url": ""},
        {"voice_id": "openai-alloy", "voice_name": "Alloy", "provider": "openai", "gender": "female", "accent": "American", "age": "Young", "preview_audio_url": ""},
        {"voice_id": "openai-echo", "voice_name": "Echo", "provider": "openai", "gender": "male", "accent": "American", "age": "Young", "preview_audio_url": ""},
        {"voice_id": "openai-nova", "voice_name": "Nova", "provider": "openai", "gender": "female", "accent": "American", "age": "Young", "preview_audio_url": ""},
    ]


def get_voices_for_language(language: str) -> list[dict]:
    """
    Return voices compatible with a given language.

    Rules:
    - English variants (en-*): all voices are compatible
    - Non-English: only voices from multilingual providers are compatible
    - Multilingual mode: all voices from multilingual providers
    """
    all_voices = list_voices()
    is_english = language.startswith("en-")

    if is_english:
        return all_voices

    # Non-English: filter to multilingual-capable providers
    compatible = []
    for v in all_voices:
        provider = v.get("provider", "").lower()
        if provider in MULTILINGUAL_PROVIDERS:
            compatible.append(v)
    return compatible


def validate_voice_language(voice_id: str, language: str) -> dict:
    """
    Validate that a voice-language combination is supported.

    Returns:
        {"valid": True} or {"valid": False, "reason": "..."}
    """
    if not voice_id:
        return {"valid": False, "reason": "No voice selected."}

    if not language or language not in SUPPORTED_LANGUAGES:
        return {"valid": False, "reason": f"Unsupported language: {language}"}

    is_english = language.startswith("en-")
    if is_english:
        # All voices work with English
        return {"valid": True}

    # Non-English: check that the voice is from a multilingual provider
    all_voices = list_voices()
    voice_info = next((v for v in all_voices if v["voice_id"] == voice_id), None)

    if not voice_info:
        # Voice not found in cache — allow it (may be a custom/cloned voice)
        return {"valid": True}

    provider = voice_info.get("provider", "").lower()
    if provider not in MULTILINGUAL_PROVIDERS:
        voice_name = voice_info.get("voice_name", voice_id)
        return {
            "valid": False,
            "reason": f'Voice "{voice_name}" (provider: {provider}) does not support {SUPPORTED_LANGUAGES.get(language, language)}. '
                      f"Please choose a voice from: {', '.join(sorted(MULTILINGUAL_PROVIDERS))}.",
        }

    return {"valid": True}


# ---------------------------------------------------------------------------
# API endpoint data helper
# ---------------------------------------------------------------------------
def get_voice_language_data() -> dict:
    """
    Return all data needed for the voice/language UI.

    Used by the JSON API endpoint to populate dropdowns dynamically.
    """
    voices = list_voices()
    languages = get_languages()

    # Build a map of language -> compatible voice IDs for client-side filtering
    voice_compat = {}
    for lang in languages:
        code = lang["code"]
        compatible = get_voices_for_language(code)
        voice_compat[code] = [v["voice_id"] for v in compatible]

    # Build defaults map
    defaults = {code: get_default_voice(code) for code in SUPPORTED_LANGUAGES}

    return {
        "voices": voices,
        "languages": languages,
        "defaults": defaults,
        "compatibility": voice_compat,
    }
