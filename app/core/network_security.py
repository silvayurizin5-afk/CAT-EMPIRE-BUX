from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import httpx


class PublicHTTPError(ValueError):
    pass


def _is_forbidden_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return True
    return any(
        (
            address.is_private,
            address.is_loopback,
            address.is_link_local,
            address.is_multicast,
            address.is_reserved,
            address.is_unspecified,
        )
    )


async def validate_public_http_url(url: str) -> str:
    try:
        parsed = urlparse(str(url).strip())
    except ValueError as exc:
        raise PublicHTTPError("URL HTTP/HTTPS inválida.") from exc

    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise PublicHTTPError("A URL precisa usar HTTP/HTTPS.")
    if parsed.username is not None or parsed.password is not None:
        raise PublicHTTPError("URLs com credenciais embutidas não são permitidas.")

    host = parsed.hostname.rstrip(".").casefold()
    if host == "localhost" or host.endswith(".localhost"):
        raise PublicHTTPError("Endereços locais não são permitidos.")

    try:
        literal = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        literal = None

    if literal is not None:
        if _is_forbidden_ip(str(literal)):
            raise PublicHTTPError("Endereços de rede privada/interna não são permitidos.")
        return parsed.geturl()

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        infos = await asyncio.to_thread(
            socket.getaddrinfo,
            host,
            port,
            type=socket.SOCK_STREAM,
        )
    except OSError as exc:
        raise PublicHTTPError("Não foi possível resolver o endereço informado.") from exc

    addresses = {str(info[4][0]).split("%", 1)[0] for info in infos if info[4]}
    if not addresses:
        raise PublicHTTPError("O endereço não possui IP público resolvível.")
    if any(_is_forbidden_ip(address) for address in addresses):
        raise PublicHTTPError("Endereços de rede privada/interna não são permitidos.")
    return parsed.geturl()


async def download_public_http_bytes(
    url: str,
    *,
    max_bytes: int,
    timeout: httpx.Timeout,
    headers: dict[str, str] | None = None,
    require_content_type_prefix: str | None = None,
    max_redirects: int = 5,
    transport: httpx.AsyncBaseTransport | None = None,
) -> bytes:
    current = str(url).strip()

    try:
        async with httpx.AsyncClient(
            follow_redirects=False,
            timeout=timeout,
            headers=headers,
            transport=transport,
        ) as client:
            for _ in range(max_redirects + 1):
                current = await validate_public_http_url(current)
                async with client.stream("GET", current) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location:
                            raise PublicHTTPError("Redirecionamento HTTP sem destino.")
                        current = urljoin(current, location)
                        continue

                    response.raise_for_status()
                    content_type = (response.headers.get("content-type") or "").casefold()
                    if (
                        require_content_type_prefix
                        and content_type
                        and not content_type.startswith(require_content_type_prefix.casefold())
                    ):
                        raise PublicHTTPError("A URL não retornou o tipo de conteúdo esperado.")

                    raw_length = response.headers.get("content-length")
                    if raw_length:
                        try:
                            if int(raw_length) > max_bytes:
                                raise PublicHTTPError("O arquivo remoto excede o limite permitido.")
                        except ValueError:
                            pass

                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > max_bytes:
                            raise PublicHTTPError("O arquivo remoto excede o limite permitido.")
                    if not data:
                        raise PublicHTTPError("A URL retornou um arquivo vazio.")
                    return bytes(data)
    except httpx.HTTPError as exc:
        raise PublicHTTPError("Falha ao acessar a URL remota.") from exc

    raise PublicHTTPError("A URL excedeu o limite de redirecionamentos.")
