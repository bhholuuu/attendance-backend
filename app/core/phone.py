import re

# A phone number stored in an international, E.164-like format:
# a leading '+' followed by a country code and national number (7-15 digits).
_PHONE_PATTERN = re.compile(r"^\+[1-9]\d{7,14}$")

# Characters commonly used for formatting that we strip before validation.
_FORMAT_CHARS = re.compile(r"[\s\-().]")


def normalize_phone_number(value: str) -> str:
    """Normalize a parent WhatsApp number to an international format.

    Removes common formatting characters (spaces, hyphens, parentheses,
    dots) and requires a leading '+' with a valid country code so that only
    obviously valid international numbers are accepted.

    Raises:
        ValueError: if the value is not a plausible international number.
    """
    if value is None:
        raise ValueError("Parent WhatsApp number is required")

    cleaned = _FORMAT_CHARS.sub("", value).strip()
    if not cleaned.startswith("+"):
        raise ValueError(
            "Parent WhatsApp number must include a country code in "
            "international format, e.g. +919876543210"
        )
    if not _PHONE_PATTERN.fullmatch(cleaned):
        raise ValueError(
            "Parent WhatsApp number is invalid. Use a valid international "
            "format, e.g. +919876543210"
        )
    return cleaned
