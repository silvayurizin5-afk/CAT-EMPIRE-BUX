from dataclasses import dataclass
from decimal import Decimal
from io import BytesIO
from uuid import UUID
import re
import unicodedata

import qrcode

from app.core.config import settings
from app.core.money import money, require_positive


class PixConfigError(ValueError):
    pass


@dataclass(slots=True, frozen=True)
class PixConfig:
    key: str
    receiver_name: str
    receiver_city: str


@dataclass(slots=True, frozen=True)
class PixCharge:
    payload: str
    qr_png: bytes
    txid: str
    amount_brl: Decimal


def _ascii_upper(value: str, *, max_length: int) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[^A-Z0-9 .-]", "", ascii_value.upper())
    return " ".join(cleaned.split())[:max_length]


def _tlv(tag: str, value: str) -> str:
    length = len(value.encode("utf-8"))
    if length > 99:
        raise ValueError(f"Campo PIX {tag} excede o limite de 99 bytes")
    return f"{tag}{length:02d}{value}"


def crc16_ccitt(value: str) -> str:
    crc = 0xFFFF
    for byte in value.encode("utf-8"):
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return f"{crc:04X}"


def validate_pix_config() -> PixConfig:
    key = settings.pix_key.get_secret_value().strip()
    name = _ascii_upper(settings.pix_receiver_name, max_length=25)
    city = _ascii_upper(settings.pix_receiver_city, max_length=15)
    if not key:
        raise PixConfigError("PIX_KEY não configurada")
    if len(key.encode("utf-8")) > 77:
        raise PixConfigError("PIX_KEY excede o limite permitido pelo BR Code")
    if not name:
        raise PixConfigError("PIX_RECEIVER_NAME não configurado")
    if not city:
        raise PixConfigError("PIX_RECEIVER_CITY não configurada")
    return PixConfig(key=key, receiver_name=name, receiver_city=city)


def txid_for_order(order_id: UUID) -> str:
    return f"NB{order_id.hex[:20].upper()}"


def build_pix_payload(
    *,
    amount_brl: Decimal,
    txid: str,
    config: PixConfig | None = None,
) -> str:
    config = config or validate_pix_config()
    amount = require_positive(money(amount_brl))
    clean_txid = re.sub(r"[^A-Za-z0-9]", "", txid)[:25]
    if not clean_txid:
        raise ValueError("TXID do PIX inválido")

    merchant_account = _tlv("00", "BR.GOV.BCB.PIX") + _tlv("01", config.key)
    additional_data = _tlv("05", clean_txid)
    payload_without_crc = "".join(
        [
            _tlv("00", "01"),
            _tlv("01", "11"),
            _tlv("26", merchant_account),
            _tlv("52", "0000"),
            _tlv("53", "986"),
            _tlv("54", f"{amount:.2f}"),
            _tlv("58", "BR"),
            _tlv("59", config.receiver_name),
            _tlv("60", config.receiver_city),
            _tlv("62", additional_data),
            "6304",
        ]
    )
    return payload_without_crc + crc16_ccitt(payload_without_crc)


def render_pix_qr_png(payload: str) -> bytes:
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=8, border=4)
    qr.add_data(payload)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def create_pix_charge(*, amount_brl: Decimal, order_id: UUID) -> PixCharge:
    config = validate_pix_config()
    txid = txid_for_order(order_id)
    amount = require_positive(money(amount_brl))
    payload = build_pix_payload(amount_brl=amount, txid=txid, config=config)
    return PixCharge(
        payload=payload,
        qr_png=render_pix_qr_png(payload),
        txid=txid,
        amount_brl=amount,
    )
