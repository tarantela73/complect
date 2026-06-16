from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from apps.integration.dadata_client import AddressSuggestion, suggest_address

_DADATA_RESPONSE = {
    "suggestions": [
        {
            "value": "г Краснодар, ул Северная, д 1",
            "data": {"geo_lat": "45.035470", "geo_lon": "38.975313"},
        },
        {
            "value": "г Краснодар, ул Северная, д 2",
            "data": {"geo_lat": "45.035500", "geo_lon": "38.975400"},
        },
    ]
}


@pytest.mark.asyncio
async def test_suggest_address_returns_suggestions(settings):
    settings.DADATA_API_KEY = "test-key"

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = MagicMock(return_value=_DADATA_RESPONSE)

    with patch("apps.integration.dadata_client.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        result = await suggest_address("Краснодар Северная")

    assert len(result) == 2
    assert isinstance(result[0], AddressSuggestion)
    assert result[0].label == "г Краснодар, ул Северная, д 1"
    assert result[0].lat == 45.035470
    assert result[0].lon == 38.975313


@pytest.mark.asyncio
async def test_suggest_address_empty_query(settings):
    settings.DADATA_API_KEY = "test-key"
    result = await suggest_address("")
    assert result == []


@pytest.mark.asyncio
async def test_suggest_address_no_api_key(settings):
    settings.DADATA_API_KEY = ""
    result = await suggest_address("Краснодар")
    assert result == []


@pytest.mark.asyncio
async def test_suggest_address_timeout_returns_empty(settings):
    settings.DADATA_API_KEY = "test-key"

    with patch("apps.integration.dadata_client.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
        mock_client_cls.return_value = mock_client

        result = await suggest_address("Краснодар")

    assert result == []


@pytest.mark.asyncio
async def test_suggest_address_http_error_returns_empty(settings):
    settings.DADATA_API_KEY = "test-key"

    with patch("apps.integration.dadata_client.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(
            side_effect=httpx.HTTPStatusError("error", request=MagicMock(), response=MagicMock())
        )
        mock_client_cls.return_value = mock_client

        result = await suggest_address("Краснодар")

    assert result == []
