import httpx
from django.conf import settings

TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
TIMEOUT_SECONDS = 5.0


def verify_turnstile(token: str, remote_ip: str = "") -> bool:
    """
    Server-side Cloudflare Turnstile verification (ADR-0023, KTD-4).
    Returns True when the token is valid. Raises httpx.TimeoutException on timeout.
    """
    secret = getattr(settings, "CF_TURNSTILE_SECRET_KEY", "")
    payload = {"secret": secret, "response": token}
    if remote_ip:
        payload["remoteip"] = remote_ip

    with httpx.Client(timeout=TIMEOUT_SECONDS) as client:
        resp = client.post(TURNSTILE_VERIFY_URL, data=payload)
        resp.raise_for_status()
        data = resp.json()

    return bool(data.get("success", False))
