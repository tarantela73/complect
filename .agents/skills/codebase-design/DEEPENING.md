# Deepening

How to deepen a cluster of shallow modules safely, given its dependencies. Assumes the vocabulary in [SKILL.md](SKILL.md): **module**, **interface**, **seam**, **adapter**.

## Dependency categories

When assessing a candidate for deepening, classify its dependencies. The category determines how the deepened module is tested across its seam.

### 1. In-process

Pure computation, in-memory state, no I/O. Consider consolidation within the owning context and test through the retained public contract; in-process deployment does not authorize merging bounded contexts.

### 2. Local-substitutable

Dependencies may have local stand-ins for pure logic tests (for example an in-memory filesystem). For PostgreSQL/RLS/locking/migration/Outbox semantics, use real Docker PostgreSQL/PostGIS. Stand-in availability does not determine bounded-context or transaction boundaries; preserve required public contracts.

### 3. Remote but owned (Ports & Adapters)

Your own services across a network boundary (microservices, internal APIs). Define a **port** (interface) at the seam. The deep module owns the logic; the transport is injected as an **adapter**. Tests use an in-memory adapter. Production uses an HTTP/gRPC/queue adapter.

Recommendation shape: *"Define a port at the seam, implement an HTTP adapter for production and an in-memory adapter for testing, so the logic sits in one deep module even though it's deployed across a network."*

### 4. True external (Mock)

Third-party services (Stripe, Twilio, etc.) you don't control. The deepened module takes the external dependency as an injected port; tests provide a mock adapter.

## Seam discipline

- **Preserve required seams regardless of adapter count.** Apply the Complect exception in [SKILL.md](SKILL.md): ownership, accepted contracts, event/transaction boundaries and established test architecture justify a single-implementation Protocol.
- **Internal seams vs external seams.** A deep module can have internal seams (private to its implementation, used by its own tests) as well as the external seam at its interface. Don't expose internal seams through the interface just because tests use them.

## Testing strategy: replace, don't layer

- Remove an old test only after proving its behavior and failure paths remain covered at the retained contract. Preserve independent security, database and concurrency regression evidence.
- Write new tests at the deepened module's interface. The **interface is the test surface**.
- Tests assert on observable outcomes through the interface, not internal state.
- Tests should survive internal refactors, since they describe behaviour, not implementation. If a test has to change when the implementation changes, it's testing past the interface.
