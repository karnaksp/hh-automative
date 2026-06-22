"""Compatibility constants backed by config/resumes.json."""

from hh_automative.settings import Settings, load_resume_profile

_profile = load_resume_profile(Settings.load())

DEFAULT_RESUME = _profile.default_resume
RESUME_CODES = _profile.resume_codes
