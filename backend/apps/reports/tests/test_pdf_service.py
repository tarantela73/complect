"""Tests for PDF rendering service — contract and contragent print forms (U12, U13)."""
from __future__ import annotations

import sys
import uuid
from unittest.mock import MagicMock, patch

import pytest

from apps.common.tenancy.context import set_tenant_context
from apps.common.tenancy.context_core import RequestContext, set_context
from apps.contragents.models.contragent import Contragent, ContragentType
from apps.contracts.models.contract import Contract, ResourceSupplyType


def _ctx(tenant_id: uuid.UUID) -> None:
    set_context(RequestContext(
        tenant_id=tenant_id,
        user_id=uuid.uuid4(),
        request_id=uuid.uuid4(),
        correlation_id=uuid.uuid4(),
    ))
    set_tenant_context(tenant_id)


def _make_contragent(tenant_id: uuid.UUID, name: str = "ООО Тест", inn: str = "7700000001") -> Contragent:
    ct = Contragent(
        name=name,
        inn=inn,
        contragent_type=ContragentType.LEGAL_ENTITY,
        tenant_id=tenant_id,
    )
    ct.save()
    return ct


def _make_contract(
    tenant_id: uuid.UUID,
    contragent_id: uuid.UUID | None = None,
    total_amount: int = 100_000,
    paid_amount: int = 40_000,
) -> Contract:
    c = Contract(
        contract_number=f"C-{uuid.uuid4().hex[:6].upper()}",
        title="Test Contract",
        status="ACTIVE",
        resource_type=ResourceSupplyType.ELECTRICITY,
        total_amount=total_amount,
        paid_amount=paid_amount,
        tenant_id=tenant_id,
        contragent_id=contragent_id,
    )
    c.save()
    return c


FAKE_PDF = b"%PDF-1.4 fake"


def _mock_weasyprint(pdf_bytes: bytes = FAKE_PDF) -> MagicMock:
    """Build a sys.modules-level weasyprint mock that returns pdf_bytes from write_pdf."""
    mock_wp = MagicMock()
    mock_wp.HTML.return_value.write_pdf.return_value = pdf_bytes
    return mock_wp


def _capturing_weasyprint() -> tuple[MagicMock, list[str]]:
    """Build a weasyprint mock that captures HTML string args passed to HTML()."""
    captured: list[str] = []

    def html_factory(string: str) -> MagicMock:
        captured.append(string)
        m = MagicMock()
        m.write_pdf.return_value = FAKE_PDF
        return m

    mock_wp = MagicMock()
    mock_wp.HTML.side_effect = html_factory
    return mock_wp, captured


@pytest.mark.django_db
class TestRenderContractPdf:
    @pytest.fixture(autouse=True)
    def setup(self):
        self.tenant_id = uuid.uuid4()
        _ctx(self.tenant_id)

    def test_returns_pdf_bytes(self):
        ct = _make_contragent(self.tenant_id)
        contract = _make_contract(self.tenant_id, contragent_id=ct.id)

        with patch.dict(sys.modules, {"weasyprint": _mock_weasyprint()}):
            from apps.reports.pdf_service import render_contract_pdf
            result = render_contract_pdf(contract.id)

        assert result == FAKE_PDF

    def test_html_rendered_with_contract_data(self):
        ct = _make_contragent(self.tenant_id, name="ООО Ромашка", inn="7712345678")
        contract = _make_contract(self.tenant_id, contragent_id=ct.id)

        mock_wp, captured = _capturing_weasyprint()
        with patch.dict(sys.modules, {"weasyprint": mock_wp}):
            from apps.reports.pdf_service import render_contract_pdf
            render_contract_pdf(contract.id)

        assert len(captured) == 1
        html = captured[0]
        assert "ООО Ромашка" in html
        assert "7712345678" in html
        assert contract.contract_number in html

    def test_contract_without_contragent(self):
        contract = _make_contract(self.tenant_id, contragent_id=None)

        with patch.dict(sys.modules, {"weasyprint": _mock_weasyprint()}):
            from apps.reports.pdf_service import render_contract_pdf
            result = render_contract_pdf(contract.id)

        assert result == FAKE_PDF

    def test_permission_denied_for_missing_contract(self):
        from django.core.exceptions import PermissionDenied
        with patch.dict(sys.modules, {"weasyprint": _mock_weasyprint()}):
            from apps.reports.pdf_service import render_contract_pdf
            with pytest.raises(PermissionDenied):
                render_contract_pdf(uuid.uuid4())

    def test_anti_idor_other_tenant_contract(self):
        other_tenant = uuid.uuid4()
        _ctx(other_tenant)
        contract = _make_contract(other_tenant)

        _ctx(self.tenant_id)

        from django.core.exceptions import PermissionDenied
        with patch.dict(sys.modules, {"weasyprint": _mock_weasyprint()}):
            from apps.reports.pdf_service import render_contract_pdf
            with pytest.raises(PermissionDenied):
                render_contract_pdf(contract.id)


@pytest.mark.django_db
class TestRenderContragentPdf:
    @pytest.fixture(autouse=True)
    def setup(self):
        self.tenant_id = uuid.uuid4()
        _ctx(self.tenant_id)

    def test_returns_pdf_bytes(self):
        ct = _make_contragent(self.tenant_id)

        with patch.dict(sys.modules, {"weasyprint": _mock_weasyprint()}):
            from apps.reports.pdf_service import render_contragent_pdf
            result = render_contragent_pdf(ct.id)

        assert result == FAKE_PDF

    def test_html_rendered_with_contragent_data(self):
        ct = _make_contragent(self.tenant_id, name="ИП Иванов", inn="123456789012")

        mock_wp, captured = _capturing_weasyprint()
        with patch.dict(sys.modules, {"weasyprint": mock_wp}):
            from apps.reports.pdf_service import render_contragent_pdf
            render_contragent_pdf(ct.id)

        assert len(captured) == 1
        html = captured[0]
        assert "ИП Иванов" in html
        assert "123456789012" in html

    def test_linked_contracts_count_in_context(self):
        ct = _make_contragent(self.tenant_id)
        _make_contract(self.tenant_id, contragent_id=ct.id)
        _make_contract(self.tenant_id, contragent_id=ct.id)

        mock_wp, captured = _capturing_weasyprint()
        with patch.dict(sys.modules, {"weasyprint": mock_wp}):
            from apps.reports.pdf_service import render_contragent_pdf
            render_contragent_pdf(ct.id)

        assert "2" in captured[0]

    def test_permission_denied_for_missing_contragent(self):
        from django.core.exceptions import PermissionDenied
        with patch.dict(sys.modules, {"weasyprint": _mock_weasyprint()}):
            from apps.reports.pdf_service import render_contragent_pdf
            with pytest.raises(PermissionDenied):
                render_contragent_pdf(uuid.uuid4())

    def test_anti_idor_other_tenant_contragent(self):
        other_tenant = uuid.uuid4()
        _ctx(other_tenant)
        ct = _make_contragent(other_tenant, inn="9900000001")

        _ctx(self.tenant_id)

        from django.core.exceptions import PermissionDenied
        with patch.dict(sys.modules, {"weasyprint": _mock_weasyprint()}):
            from apps.reports.pdf_service import render_contragent_pdf
            with pytest.raises(PermissionDenied):
                render_contragent_pdf(ct.id)
