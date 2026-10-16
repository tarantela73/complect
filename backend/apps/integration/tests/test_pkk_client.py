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

_PKK_RESPONSE_EMPTY: dict[str, list[object]] = {"features": []}


def _mock_success_response(json_body):
    mock_resp = MagicMock()
    mock_resp.is_success = True
    mock_resp.json = MagicMock(return_value=json_body)
    return mock_resp


def _mock_fail_response(status_code):
    mock_resp = MagicMock()
    mock_resp.is_success = False
    mock_resp.status_code = status_code
    return mock_resp


def _make_client_mock(response):
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=response)
    return mock_client


@pytest.mark.asyncio
@pytest.mark.parametrize("center_as_strings", [False, True])
async def test_search_cadastre_returns_result(center_as_strings):
    with patch("apps.integration.pkk_client.httpx.AsyncClient") as mock_cls:
        response = {
            "features": [dict(_PKK_RESPONSE_WITH_GEOMETRY["features"][0])],
        }
        if center_as_strings:
            response["features"][0]["attrs"] = {"center": {"x": "38.975313", "y": "45.035470"}}
        mock_cls.return_value = _make_client_mock(_mock_success_response(response))

        result = await search_cadastre("23:43:0123456:789")

    assert isinstance(result, CadastreResult)
    assert result.center_lat == 45.035470
    assert result.center_lon == 38.975313
    assert result.geometry is not None
    assert result.geometry["type"] == "Polygon"


@pytest.mark.asyncio
async def test_search_cadastre_empty_features():
    with patch("apps.integration.pkk_client.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = _make_client_mock(_mock_success_response(_PKK_RESPONSE_EMPTY))

        result = await search_cadastre("00:00:0000000:000")

    assert result is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "geometry",
    [None, {"type": "Point", "coordinates": [38.9, 45.0]}, {"type": "Polygon", "coordinates": []}],
)
async def test_search_cadastre_without_center_or_polygon_returns_none(geometry):
    response = {"features": [{"attrs": {}, "geometry": geometry}]}
    with patch("apps.integration.pkk_client.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = _make_client_mock(_mock_success_response(response))
        result = await search_cadastre("23:43:0123456:789")

    assert result is None


@pytest.mark.asyncio
async def test_search_cadastre_empty_query():
    result = await search_cadastre("")
    assert result is None


@pytest.mark.asyncio
async def test_search_cadastre_timeout_returns_none():
    with patch("apps.integration.pkk_client.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
        mock_cls.return_value = mock_client

        result = await search_cadastre("23:43:0123456:789")

    assert result is None


@pytest.mark.asyncio
async def test_search_cadastre_http_error_returns_none():
    with patch("apps.integration.pkk_client.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(
            side_effect=httpx.HTTPStatusError("error", request=MagicMock(), response=MagicMock())
        )
        mock_cls.return_value = mock_client

        result = await search_cadastre("23:43:0123456:789")

    assert result is None


@pytest.mark.asyncio
async def test_search_cadastre_redirect_returns_none():
    """pkk.rosreestr.ru → 301 redirect (НСПД migration). follow_redirects=False → is_success=False → None."""
    with patch("apps.integration.pkk_client.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = _make_client_mock(_mock_fail_response(301))

        result = await search_cadastre("77:01:0001001:1")

    assert result is None


@pytest.mark.asyncio
async def test_search_cadastre_forbidden_returns_none():
    """НСПД /api/features returns 403 without session auth → None."""
    with patch("apps.integration.pkk_client.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = _make_client_mock(_mock_fail_response(403))

        result = await search_cadastre("77:01:0001001:1")

    assert result is None
