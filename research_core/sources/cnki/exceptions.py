"""CNKI search errors."""


class CnkiSearchError(Exception):
    """Base CNKI search failure."""


class CnkiCaptchaError(CnkiSearchError):
    """Tencent captcha appeared; user must solve it in the browser."""


class CnkiConfigError(CnkiSearchError):
    """CNKI integration is not configured or disabled."""


class CnkiTimeoutError(CnkiSearchError):
    """Page or results did not load in time."""
