"""Rule pack. Importing this module registers every check."""

from . import aai_login, aup_pointer, english_available  # noqa: F401

__all__ = ["aai_login", "aup_pointer", "english_available"]
