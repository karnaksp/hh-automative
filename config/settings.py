"""Compatibility constants backed by hh_automative settings."""

from hh_automative.settings import Settings, load_search_profile

_settings = Settings.load()
_profile = load_search_profile(_settings)

USERNAME = _settings.username
PASSWORD = _settings.password
JOB_SEARCH_QUERY = _profile.query
EXCLUDE = _profile.exclude
REGION = _profile.region
MIN_SALARY = _profile.min_salary
ONLY_WITH_SALARY = _profile.only_with_salary

COOKIES_PATH = str(_settings.cookies_path)
LOCAL_STORAGE_PATH = str(_settings.local_storage_path)
LOGIN_PAGE = _settings.login_page
SEARCH_LINK = _settings.search_link
ADVANCED_SEARCH_URL_QUERY = _profile.advanced_search_url
