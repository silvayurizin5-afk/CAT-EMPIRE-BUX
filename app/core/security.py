import hashlib
import hmac
import time


def parse_signature_header(value: str) -> tuple[str | None, str | None]:
    parts: dict[str, str] = {}
    for item in value.split(","):
        key, sep, raw_value = item.strip().partition("=")
        if sep and key and raw_value:
            parts[key] = raw_value
    return parts.get("ts"), parts.get("v1")


def verify_mercado_pago_signature(
    *,
    signature_header: str,
    request_id: str,
    data_id: str,
    secret: str,
    tolerance_seconds: int = 300,
    now: int | None = None,
) -> bool:
    if not signature_header or not request_id or not data_id or not secret:
        return False

    ts, received = parse_signature_header(signature_header)
    if not ts or not received:
        return False

    try:
        timestamp = int(ts)
    except ValueError:
        return False

    current = int(time.time()) if now is None else now
    timestamp_seconds = timestamp // 1000 if timestamp > 10_000_000_000 else timestamp
    if tolerance_seconds > 0 and abs(current - timestamp_seconds) > tolerance_seconds:
        return False

    normalized_id = data_id.lower()
    template = f"id:{normalized_id};request-id:{request_id};ts:{ts};"
    expected = hmac.new(secret.encode(), template.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, received)
