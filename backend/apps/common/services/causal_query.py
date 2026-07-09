import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import structlog
from django.db.models import Q
from django.utils import timezone as tz

from apps.common.models.outbox import OutboxRecord
from apps.common.models.processed_event import ProcessedEvent

logger = structlog.get_logger(__name__)

ProcessingStatus = Literal["pending", "claimed", "acked", "dlq"]


@dataclass(slots=True, frozen=True)
class CausalNode:
    outbox_record_id: uuid.UUID
    causal_root_id: uuid.UUID
    correlation_id: uuid.UUID
    tenant_id: uuid.UUID
    occurred_at: datetime
    event_type: str
    # NULL until ADR-0012 implements aggregate_seq on OutboxRecord.
    aggregate_id: str | None
    aggregate_type: str | None
    aggregate_seq: int | None
    # NULL when OutboxRecord is archived and only ProcessedEvent evidence remains.
    processing_status: ProcessingStatus | None
    outbox_archived: bool


@dataclass(slots=True, frozen=True)
class CausalChain:
    root_id: uuid.UUID
    nodes: tuple[CausalNode, ...]
    # False if any node is not acked, or if OutboxRecord was archived before query.
    # Callers MUST handle False — absence of data is a documented state (ADR-0016).
    chain_complete: bool
    # Names of layers that returned no rows for this chain.
    missing_layers: tuple[str, ...]
    queried_at: datetime
    queried_by: str


def _derive_status(
    outbox_row: dict,
    processed_events_by_outbox_id: dict[uuid.UUID, dict],
) -> ProcessingStatus:
    pe = processed_events_by_outbox_id.get(outbox_row["id"])
    if pe is not None:
        return "acked" if pe["acked_at"] is not None else "claimed"
    if outbox_row["failed_permanently_at"] is not None:
        return "dlq"
    return "pending"


class CausalQueryService:
    """
    Read-only causal query surface (ADR-0016).

    Does NOT write, repair, or replay. Internal diagnostic use only.
    Finance tenant queries are audit-logged per rule 5.8.
    """

    @staticmethod
    def get_chain_by_root(
        causal_root_id: uuid.UUID,
        tenant_id: uuid.UUID,
        queried_by: str,
    ) -> CausalChain:
        """
        Class A: full causal chain by causal_root_id.

        Returns all OutboxRecord nodes with this causal_root_id.
        Falls back to ProcessedEvent when OutboxRecord is archived (chain_complete=False).
        """
        # causal_root_id == record.id for root; == original root for repair/compensation.
        outbox_rows = list(
            OutboxRecord.objects.filter(
                Q(causal_root_id=causal_root_id) | Q(id=causal_root_id),
            ).values(
                "id",
                "causal_root_id",
                "correlation_id",
                "event_type",
                "created_at",
                "is_processed",
                "failed_permanently_at",
                "payload_json",
            )
        )

        # Dedup: a root record satisfies both Q branches.
        outbox_by_id: dict[uuid.UUID, dict] = {r["id"]: r for r in outbox_rows}
        outbox_ids = set(outbox_by_id.keys())

        # One ProcessedEvent per outbox_record_id — prefer acked over claimed.
        pe_by_outbox_id: dict[uuid.UUID, dict] = {}
        if outbox_ids:
            for pe in ProcessedEvent.objects.filter(
                outbox_record_id__in=outbox_ids,
            ).values("outbox_record_id", "acked_at"):
                oid = pe["outbox_record_id"]
                existing = pe_by_outbox_id.get(oid)
                if existing is None or (pe["acked_at"] is not None and existing["acked_at"] is None):
                    pe_by_outbox_id[oid] = pe

        nodes: list[CausalNode] = []
        for row in outbox_by_id.values():
            record_tenant = row["payload_json"].get("tenant_id")
            nodes.append(
                CausalNode(
                    outbox_record_id=row["id"],
                    causal_root_id=row["causal_root_id"] or causal_root_id,
                    correlation_id=row["correlation_id"],
                    tenant_id=uuid.UUID(record_tenant) if record_tenant else tenant_id,
                    occurred_at=row["created_at"],
                    event_type=row["event_type"],
                    aggregate_id=None,
                    aggregate_type=None,
                    aggregate_seq=None,
                    processing_status=_derive_status(row, pe_by_outbox_id),
                    outbox_archived=False,
                )
            )

        # Post-archival evidence: ProcessedEvent records whose OutboxRecord is gone.
        archived_pe_outbox_ids = (
            set(
                ProcessedEvent.objects.filter(
                    causal_root_id=causal_root_id,
                ).values_list("outbox_record_id", flat=True)
            )
            - outbox_ids
        )

        for archived_id in archived_pe_outbox_ids:
            nodes.append(
                CausalNode(
                    outbox_record_id=archived_id,
                    causal_root_id=causal_root_id,
                    correlation_id=uuid.UUID(int=0),
                    tenant_id=tenant_id,
                    occurred_at=tz.now(),
                    event_type="<archived>",
                    aggregate_id=None,
                    aggregate_type=None,
                    aggregate_seq=None,
                    processing_status="acked",
                    outbox_archived=True,
                )
            )

        missing_layers: list[str] = []
        if not outbox_by_id and not archived_pe_outbox_ids:
            missing_layers.append("outbox")

        chain_complete = (
            bool(nodes) and not archived_pe_outbox_ids and all(n.processing_status == "acked" for n in nodes)
        )

        result = CausalChain(
            root_id=causal_root_id,
            nodes=tuple(sorted(nodes, key=lambda n: n.occurred_at)),
            chain_complete=chain_complete,
            missing_layers=tuple(missing_layers),
            queried_at=tz.now(),
            queried_by=queried_by,
        )

        logger.info(
            "causal_query_executed",
            query_class="chain_by_root",
            causal_root_id=str(causal_root_id),
            tenant_id=str(tenant_id),
            queried_by=queried_by,
            chain_complete=result.chain_complete,
            node_count=len(result.nodes),
        )

        return result

    @staticmethod
    def get_chain_by_correlation(
        correlation_id: uuid.UUID,
        tenant_id: uuid.UUID,
        queried_by: str,
    ) -> CausalChain:
        """
        Class B: all events within a correlation_id (HTTP request / business operation).

        Does not depend on causal_root_id — works via OutboxRecord.correlation_id.
        No single root_id: uses correlation_id as the chain identifier.
        """
        outbox_rows = list(
            OutboxRecord.objects.filter(
                correlation_id=correlation_id,
            ).values(
                "id",
                "causal_root_id",
                "correlation_id",
                "event_type",
                "created_at",
                "is_processed",
                "failed_permanently_at",
                "payload_json",
            )
        )

        outbox_by_id: dict[uuid.UUID, dict] = {r["id"]: r for r in outbox_rows}
        outbox_ids = set(outbox_by_id.keys())

        pe_by_outbox_id: dict[uuid.UUID, dict] = {}
        if outbox_ids:
            for pe in ProcessedEvent.objects.filter(
                outbox_record_id__in=outbox_ids,
            ).values("outbox_record_id", "acked_at"):
                oid = pe["outbox_record_id"]
                existing = pe_by_outbox_id.get(oid)
                if existing is None or (pe["acked_at"] is not None and existing["acked_at"] is None):
                    pe_by_outbox_id[oid] = pe

        nodes: list[CausalNode] = []
        for row in outbox_by_id.values():
            record_tenant = row["payload_json"].get("tenant_id")
            nodes.append(
                CausalNode(
                    outbox_record_id=row["id"],
                    causal_root_id=row["causal_root_id"] or row["id"],
                    correlation_id=row["correlation_id"],
                    tenant_id=uuid.UUID(record_tenant) if record_tenant else tenant_id,
                    occurred_at=row["created_at"],
                    event_type=row["event_type"],
                    aggregate_id=None,
                    aggregate_type=None,
                    aggregate_seq=None,
                    processing_status=_derive_status(row, pe_by_outbox_id),
                    outbox_archived=False,
                )
            )

        missing_layers: list[str] = []
        if not nodes:
            missing_layers.append("outbox")

        chain_complete = bool(nodes) and all(n.processing_status == "acked" for n in nodes)

        # Class B uses correlation_id as logical root since there may be multiple causal roots.
        result = CausalChain(
            root_id=correlation_id,
            nodes=tuple(sorted(nodes, key=lambda n: n.occurred_at)),
            chain_complete=chain_complete,
            missing_layers=tuple(missing_layers),
            queried_at=tz.now(),
            queried_by=queried_by,
        )

        logger.info(
            "causal_query_executed",
            query_class="chain_by_correlation",
            correlation_id=str(correlation_id),
            tenant_id=str(tenant_id),
            queried_by=queried_by,
            chain_complete=result.chain_complete,
            node_count=len(result.nodes),
        )

        return result

    @staticmethod
    def get_aggregate_history(
        tenant_id: uuid.UUID,
        aggregate_type: str,
        aggregate_id: uuid.UUID,
        queried_by: str,
        cursor: int | None = None,
        limit: int = 50,
    ) -> CausalChain:
        """
        Class C: aggregate event history ordered by aggregate_seq (ADR-0012).

        Cursor pagination: WHERE aggregate_seq < cursor ORDER BY aggregate_seq DESC.
        Only returns records with aggregate_seq IS NOT NULL (populated post-ADR-0012).

        If no records have aggregate_seq set, returns empty chain with
        missing_layers=("aggregate_seq_unavailable",) — call site must handle.
        """
        qs = OutboxRecord.objects.filter(
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            aggregate_seq__isnull=False,
            payload_json__tenant_id=str(tenant_id),
        )
        if cursor is not None:
            qs = qs.filter(aggregate_seq__lt=cursor)

        outbox_rows = list(
            qs.values(
                "id",
                "causal_root_id",
                "correlation_id",
                "event_type",
                "created_at",
                "is_processed",
                "failed_permanently_at",
                "aggregate_id",
                "aggregate_type",
                "aggregate_seq",
            ).order_by("-aggregate_seq")[:limit]
        )

        outbox_by_id: dict[uuid.UUID, dict] = {r["id"]: r for r in outbox_rows}
        outbox_ids = set(outbox_by_id.keys())

        pe_by_outbox_id: dict[uuid.UUID, dict] = {}
        if outbox_ids:
            for pe in ProcessedEvent.objects.filter(
                outbox_record_id__in=outbox_ids,
            ).values("outbox_record_id", "acked_at"):
                oid = pe["outbox_record_id"]
                existing = pe_by_outbox_id.get(oid)
                if existing is None or (pe["acked_at"] is not None and existing["acked_at"] is None):
                    pe_by_outbox_id[oid] = pe

        nodes: list[CausalNode] = []
        for row in outbox_by_id.values():
            nodes.append(
                CausalNode(
                    outbox_record_id=row["id"],
                    causal_root_id=row["causal_root_id"] or row["id"],
                    correlation_id=row["correlation_id"],
                    tenant_id=tenant_id,
                    occurred_at=row["created_at"],
                    event_type=row["event_type"],
                    aggregate_id=str(row["aggregate_id"]) if row["aggregate_id"] else None,
                    aggregate_type=row["aggregate_type"],
                    aggregate_seq=row["aggregate_seq"],
                    processing_status=_derive_status(row, pe_by_outbox_id),
                    outbox_archived=False,
                )
            )

        missing_layers: list[str] = []
        if not nodes:
            missing_layers.append("aggregate_seq_unavailable")

        # Class C nodes are already ordered by -aggregate_seq from DB;
        # re-sort ascending for consistent CausalChain ordering.
        nodes.sort(key=lambda n: n.aggregate_seq or 0)

        chain_complete = bool(nodes) and all(n.processing_status == "acked" for n in nodes)

        result = CausalChain(
            root_id=aggregate_id,
            nodes=tuple(nodes),
            chain_complete=chain_complete,
            missing_layers=tuple(missing_layers),
            queried_at=tz.now(),
            queried_by=queried_by,
        )

        logger.info(
            "causal_query_executed",
            query_class="aggregate_history",
            tenant_id=str(tenant_id),
            aggregate_type=aggregate_type,
            aggregate_id=str(aggregate_id),
            queried_by=queried_by,
            chain_complete=result.chain_complete,
            node_count=len(result.nodes),
            cursor=cursor,
        )

        return result
