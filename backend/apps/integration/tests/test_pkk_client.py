from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from apps.integration.pkk_client import CadastreResult, search_cadastre

_PKK_RESPONSE_WITH_GEOMETRY = {
    "features": [
        {
            "attrs": {
                "center": {"x": 38.975313, "y": 45.035470},
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [[38.974, 45.034], [38.976, 45.034], [38.976, 45.036], [38.974, 45.036]],
                ],
            },
        }
    ]
}

_PKK_RESPONSE_EMPTY = {"features": []}


@pytest.mark.asyncio
async def test_search_cadastre_returns_result():
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = MagicMock(return_value=_PKK_RESPONSE_WITH_GEOMETRY)

    with patch("apps.integration.pkk_client.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        result = await search_cadastre("23:43:0123456:789")

    assert isinstance(result, CadastreResult)
    assert result.center_lat == 45.035470
    assert result.center_lon == 38.975313
    assert result.geometry is not None
    assert result.geometry["type"] == "Polygon"


@pytest.mark.asyncio
async def test_search_cadastre_empty_features():
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = MagicMock(return_value=_PKK_RESPONSE_EMPTY)

    with patch("apps.integration.pkk_client.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        result = await search_cadastre("00:00:0000000:000")

    assert result is None


@pytest.mark.asyncio
async def test_search_cadastre_empty_query():
    result = await search_cadastre("")
    assert result is None


@pytest.mark.asyncio
async def test_search_cadastre_timeout_returns_none():
    with patch("apps.integration.pkk_client.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
        mock_client_cls.return_value = mock_client

        result = await search_cadastre("23:43:0123456:789")

    assert result is None


@pytest.mark.asyncio
async def test_search_cadastre_http_error_returns_none():
    with patch("apps.integration.pkk_client.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(
            side_effect=httpx.HTTPStatusError("error", request=MagicMock(), response=MagicMock())
        )
        mock_client_cls.return_value = mock_client

        result = await search_cadastre("23:43:0123456:789")

    assert result is None
