import socket

import pytest

from app.core.network_security import PublicHTTPError, validate_public_http_url


@pytest.mark.asyncio
async def test_private_ip_literal_is_rejected() -> None:
    with pytest.raises(PublicHTTPError, match="privada"):
        await validate_public_http_url("http://127.0.0.1/image.gif")
    with pytest.raises(PublicHTTPError, match="privada"):
        await validate_public_http_url("http://169.254.169.254/latest/meta-data/")


@pytest.mark.asyncio
async def test_localhost_is_rejected() -> None:
    with pytest.raises(PublicHTTPError, match="locais"):
        await validate_public_http_url("http://localhost:8000/internal")


@pytest.mark.asyncio
async def test_dns_resolving_to_private_ip_is_rejected(monkeypatch) -> None:
    def fake_getaddrinfo(*args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(PublicHTTPError, match="privada"):
        await validate_public_http_url("https://cdn.example.test/banner.gif")


@pytest.mark.asyncio
async def test_public_ip_literal_is_accepted() -> None:
    assert await validate_public_http_url("https://8.8.8.8/image.png") == (
        "https://8.8.8.8/image.png"
    )
