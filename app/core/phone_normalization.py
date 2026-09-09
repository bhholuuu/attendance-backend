"""Phone number normalization helpers for outbound messaging.

These utilities normalize a raw parent WhatsApp number to a single canonical
E.164 international format before it is passed to the WhatsApp Cloud API.
They deliberately do NOT guess a country code: a number without an explicit
leading '+' country code is rejected rather than silently assigned a default
country, so no message is ever sent to the wrong recipient.
"""

from app.core.phone import normalize_phone_number as _base_normalize


def normalize_e164(value: str) -> str:
    """Return ``value`` as a canonical E.164 phone number.

    Strips common separator characters (spaces, hyphens, parentheses, dots)
    and requires a leading '+', a valid-looking country code, and a national
    number of 7-15 digits.

    Raises:
        ValueError: if the value is missing, empty, or not a plausible
            international number with an explicit country code.
    """
    return _base_normalize(value)


def is_valid_e164(value: str) -> bool:
    """Return whether ``value`` is a plausible international phone number.

    Never guesses a country code: numbers without a leading '+' are rejected.
    """
    if value is None or not str(value).strip():
        return False
    try:
        normalize_e164(value)
        return True
    except ValueError:
        return False
