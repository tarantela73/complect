import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

import structlog
from django.conf import settings
from django.utils import timezone as tz

logger = structlog.get_logger(__name__)

DivergenceState = Literal["OBSERVABLE", "DETECTABLE", "IRREVERSIBLE"]


@dataclass(slots=True, frozen=True)
class CausalPairDefinition:
    name: str
    source_event: str
    expected_event: str
    correlation_field: str
    expected_within_seconds: int
    finalization_boundary_source: str
    authority_on_conflict: str  # "finance" | "contracts" | "require_human"
    repair_requires_sign_off: bool


class FinalizationBoundary(Protocol):
    def is_finalized(self, correlation_field_value: str) -> bool: ...


# All event types referenced by any CausalPairDefinition must appear here.
# Adding a pair with an unregistered event type will fail the contract test.
KNOWN_EVENTS: frozenset[str] = frozenset(
    {
        "contracts.ContractClosed",
        "finance.TransactionPosted",
    }
)

CAUSAL_PAIRS: tuple[CausalPairDefinition, ...] = (
    CausalPairDefinition(
        name="contract_closed → finance_transaction_posted",
        source_event="contracts.ContractClosed",
        expected_event="finance.TransactionPosted",
        correlation_field="contract_id",
        expected_within_seconds=300,
        finalization_boundary_source="finance.AccountingPeriod.is_closed",
        authority_on_conflict="require_human",
        repair_requires_sign_off=True,
    ),
)

_PAIR_BY_SOURCE: dict[str, CausalPairDefinition] = {p.source_event: p for p in CAUSAL_PAIRS}


class CausalPairRegistry:
    """
    Registry of declared cross-domain causal pairs (ADR-0014).

    Each pair declares the source event → expected downstream event relationship,
    the SLO window, finalization boundary, and authority on conflict.
    """

    @staticmethod
    def get_by_source(event_type: str) -> CausalPairDefinition | None:
        return _PAIR_BY_SOURCE.get(event_type)

    @staticmethod
    def expected_effects_for(event_type: str) -> frozenset[str]:
        pair = _PAIR_BY_SOURCE.get(event_type)
        if pair is None:
            return frozenset()
        return frozenset({pair.expected_event})


class CausalDivergenceClassifier:
    """
    Classifies the divergence state of a cross-domain causal pair (ADR-0014).

    Three states:
    - OBSERVABLE: pair within SLO window — may still complete, alerting only.
    - DETECTABLE: beyond SLO, in retention window — repair possible.
    - IRREVERSIBLE: outside reconciliation window (retention or finalization) — audit only.
    """

    @staticmethod
    def classify(
        pair_def: CausalPairDefinition,
        source_created_at: datetime,
        finalization_boundary: FinalizationBoundary | None = None,
        correlation_field_value: str | None = None,
        reference_time: datetime | None = None,
    ) -> DivergenceState:
        now = reference_time if reference_time is not None else tz.now()
        age_seconds = (now - source_created_at).total_seconds()
        archive_window_seconds = settings.OUTBOX_ARCHIVE_WINDOW_DAYS * 86400

        # Finalization boundary overrides time-based classification.
        if (
            finalization_boundary is not None
            and correlation_field_value is not None
            and finalization_boundary.is_finalized(correlation_field_value)
        ):
            return "IRREVERSIBLE"

        if age_seconds > archive_window_seconds:
            return "IRREVERSIBLE"

        if age_seconds <= pair_def.expected_within_seconds:
            return "OBSERVABLE"

        return "DETECTABLE"


class OutboxBackedDomainEffectRegistry:
    """
    DomainEffectRegistry (ADR-0018 Protocol) backed by CAUSAL_PAIRS + OutboxRecord.

    Provides the bridge between the declarative pair registry and the
    CausalIncidentClassifier's TYPE IV (SILENT_SKIP) and TYPE II (DIVERGENCE) detection.

    expected_effects: derived from CAUSAL_PAIRS — what downstream events are expected.
    observed_effects: queries OutboxRecord — what events actually appeared downstream.
    """

    def expected_effects(self, event_type: str) -> frozenset[str]:
        return CausalPairRegistry.expected_effects_for(event_type)

    def observed_effects(self, causal_root_id: uuid.UUID) -> frozenset[str]:

        from apps.common.models.outbox import OutboxRecord

        return frozenset(
            OutboxRecord.objects.filter(
                causal_root_id=causal_root_id,
            )
            .exclude(id=causal_root_id)
            .filter(failed_permanently_at__isnull=True)
            .values_list("event_type", flat=True)
            .distinct()
        )
