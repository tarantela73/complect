---
date: 2026-06-22
topic: sprint-b-operational-readiness
---

# Sprint B — Operational Readiness

## Summary

Closes the most critical business workflow gaps so ERP becomes a daily working tool for director, managers, and accountant's assistant. Five blocks: verify Sprint A backend changes work end-to-end (B1), add Contragent 360° card with event timeline (B2), complete contract lifecycle with document type tagging and closure gate (B3), add file management for contragents (B4), and surface the existing Object↔Contragent link in the UI (B5). Sprint C (real data import) follows after these blocks are verified.

---

## Problem Frame

ERP is functionally ~80-85% complete but production-ready at ~55-60%. The gap is not missing modules — it is broken workflows in modules that already exist. Sprint A backend implementation (PATCH contragent, PATCH contract, activate contract, `contract_date`, file deletion) is believed complete but has not been verified end-to-end through the UI. Director, managers, and the accountant's assistant are blocked by: inability to edit and close contracts through proper document gates, no contragent information hub, no file type tagging, and no visibility of which objects a contragent serves. Sprint B closes these gaps.

---

## Key Decisions

- **B1 is the gate for B2–B5.** Sprint B begins with end-to-end verification of Sprint A backend changes. Discovered backend gaps are fixed before new features begin.
- **`attachment_type` lives on `ContractAttachment`, not on `FileRecord`.** Files domain stays independent; the role a document plays is contract-specific context, not a property of the file itself. Aligns with ADR-0005 (domain isolation).
- **Contract closure requires proof of document type, not just any attachment.** Attaching a random file is insufficient. The gate checks for at least one non-deleted `ContractAttachment` with `attachment_type` in `{CONTRACT_SIGNED, ACT_COMPLETED}`.
- **История = lightweight event timeline, not an audit trail.** Eight typed event types cover the business need. Full field-level before/after audit is deferred.
- **Object↔Contragent uses the existing `SupplyObject.contragent_id` field.** The field already exists as `UUIDField(null=True, blank=True, db_index=True)`. Sprint B surfaces it in the UI — no migration required. M:N (multiple contragents per object) is deferred.

---

## Actors

- A1. **Менеджер** — creates and edits contracts and contragents; attaches documents; closes contracts.
- A2. **Директор** — reviews Контрагент 360° for relationship status; approves contract closure.
- A3. **Помощник бухгалтера** — downloads Excel exports; attaches financial documents to contracts.

---

## Requirements

**B1 — QA and fixes**

- R1. Editing a contragent and saving produces a visible update in the contragent list and detail view.
- R2. Deleting a contragent triggers a soft delete; the contragent disappears from the active list.
- R3. Editing a contract (name, date) and saving produces a visible update in the contract detail view.
- R4. Activating a draft contract transitions its status to `ACTIVE` and the change is immediately reflected in the UI.
- R5. Attempting to close a contract without a required document (see R15) surfaces an actionable error, not a silent failure.
- R6. Each Excel export (contracts, payments, bank statements) downloads a correctly populated `.xlsx` file.

**B2 — Contragent 360°**

- R7. The contragent page renders five tabs: Основное, Договоры, Объекты, Файлы, История.
- R8. Договоры tab lists contracts linked to this contragent, showing status and contract date.
- R9. Объекты tab lists supply objects where `SupplyObject.contragent_id` equals this contragent's ID.
- R10. Файлы tab lists files attached directly to this contragent (via B4, R16–R18).
- R11. История tab shows a read-only chronological event timeline with these event types: contragent created, contragent fields modified (listing which fields changed), contract linked, contract unlinked, object linked, object unlinked, file attached, file deleted.

**B3 — Contract lifecycle**

- R12. The object selection field in the contract form uses async search (by name, address, or object number) when the tenant has 20 or more supply objects.
- R13. `ContractAttachment` gains an `attachment_type` field with values: `CONTRACT_SIGNED`, `ACT_COMPLETED`, `ESTIMATE`, `INVOICE`, `OTHER`; new attachments default to `OTHER`.
- R14. The contract attachments UI displays the type label next to each attached file and allows the user to select the type when attaching.
- R15. Setting a contract's status to `CLOSED` is blocked unless at least one non-deleted `ContractAttachment` for that contract has `attachment_type` in `{CONTRACT_SIGNED, ACT_COMPLETED}`; the API returns 422 with code `CONTRACT_MISSING_REQUIRED_DOCUMENT`.

**B4 — Files for contragents**

- R16. A file can be attached to a contragent, creating a `ContragentAttachment` junction record (`contragent_id`, `file_id`, `attached_by`, inheriting `BaseEnterpriseModel`).
- R17. A file can be detached from a contragent via soft delete of its `ContragentAttachment` record.
- R18. Files attached to a contragent appear in the Файлы tab of Контрагент 360° (R10).

**B5 — Object ↔ Contragent**

- R19. Object 360° displays the linked contragent name and a link to their page when `SupplyObject.contragent_id` is set.
- R20. Object 360° allows setting or clearing the contragent link via contragent name search.
- R21. Contragent 360° Объекты tab (R9) queries objects by `contragent_id` on the read replica — no additional model changes needed.

---

## Key Flows

- F1. **Contract closure gate**
  - **Trigger:** A1 or A2 attempts to set contract status to CLOSED.
  - **Steps:** API queries non-deleted `ContractAttachment` records for the contract; if none has `attachment_type` in `{CONTRACT_SIGNED, ACT_COMPLETED}` → 422 `CONTRACT_MISSING_REQUIRED_DOCUMENT`; otherwise → transitions to CLOSED and writes Outbox event.
  - **Covers:** R13, R15

- F2. **Contragent История render**
  - **Trigger:** A1 or A2 opens the История tab in Контрагент 360°.
  - **Steps:** Query fetches typed events for this contragent ordered by `occurred_at DESC`; renders each event row with actor name, event description, and timestamp.
  - **Covers:** R11

---

## Acceptance Examples

- AE1. **Closure blocked without required document** — **Covers R15.**
  - **Given:** Contract has one non-deleted attachment with `attachment_type=ESTIMATE`.
  - **When:** User attempts to set status to CLOSED.
  - **Then:** Response 422 `CONTRACT_MISSING_REQUIRED_DOCUMENT`; contract status unchanged.

- AE2. **Closure allowed with signed contract document** — **Covers R15.**
  - **Given:** Contract has one non-deleted attachment with `attachment_type=CONTRACT_SIGNED`.
  - **When:** User attempts to set status to CLOSED.
  - **Then:** Response 200; contract status = CLOSED.

- AE3. **Closure allowed when act is present alongside other types** — **Covers R15.**
  - **Given:** Contract has two non-deleted attachments: `attachment_type=INVOICE` and `attachment_type=ACT_COMPLETED`.
  - **When:** User attempts to set status to CLOSED.
  - **Then:** Response 200; contract status = CLOSED.

- AE4. **Soft-deleted required document does not satisfy gate** — **Covers R15.**
  - **Given:** Contract has one attachment with `attachment_type=CONTRACT_SIGNED` but `is_deleted=True`.
  - **When:** User attempts to set status to CLOSED.
  - **Then:** Response 422 `CONTRACT_MISSING_REQUIRED_DOCUMENT`.

- AE5. **Object link event appears in История** — **Covers R11, R21.**
  - **Given:** Object's `contragent_id` has been set to this contragent's ID (via B5, R20).
  - **When:** User opens История tab.
  - **Then:** Timeline contains an "Объект [name] привязан" event with actor and date.

---

## Scope Boundaries

**Deferred to Sprint C:**
- Import of real client data from Excel, 1С, or Windows folder structures.
- Full audit trail with field-level before/after values.

**Deferred, no target sprint:**
- M:N relationship between objects and contragents (current model uses a single FK).
- Detaching an existing file from a contract (ContractAttachment soft-delete via UI).
- Contragent merge and deduplication.

**Outside Sprint B:**
- Public site or CMS changes.
- Notifications, chat, or finance module work.

---

## Dependencies and Assumptions

- Sprint A backend (PATCH contragent, PATCH contract, activate contract, `contract_date`, file soft-delete) is assumed implemented. B1 verifies these work end-to-end; any gaps found are fixed within B1.
- `SupplyObject.contragent_id` exists in `backend/apps/objects/models/supply_object.py` as `UUIDField(null=True, blank=True, db_index=True)`. No migration needed for B5.
- `ContractAttachment` exists in `backend/apps/contracts/models/attachment.py` without `attachment_type`. B3 adds the field; migration follows Expand & Contract (ADR-0002): nullable column in Release N, `NOT NULL` constraint in Release N+2.
- `ContragentAttachment` does not yet exist. B4 creates the model and migration.
- В2 История events for B5 actions (object linked/unlinked) must be emitted by the B5 service layer — implementation of B2 and B5 must coordinate on the event emission contract.

---

## Outstanding Questions

- **Deferred to implementation:** Event storage for Contragent История. Options: dedicated `ContragentEvent` model in `apps/contragents/`, or a shared event log table. Implementation team decides based on existing patterns in the contragents domain.
- **Deferred to implementation:** Async search threshold (R12). 20 objects is the suggested cutoff; implementation may adjust after measuring actual dropdown render time.
