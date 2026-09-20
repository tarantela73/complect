# Good and Bad Tests

## Good Tests

**Integration-style**: Test through real interfaces, not mocks of internal parts.

```typescript
// GOOD: Tests observable behavior
test("user can checkout with valid cart", async () => {
  const cart = createCart();
  cart.add(product);
  const result = await checkout(cart, paymentMethod);
  expect(result.status).toBe("confirmed");
});
```

Characteristics:

- Tests behavior users/callers care about
- Uses the established public contract, including database/query/locking guarantees where specified
- Survives internal refactors
- Describes WHAT, not HOW
- One coherent behavior per test, with as many independent assertions as its contract needs

## Bad Tests

**Implementation-detail tests**: Coupled to internal structure.

```typescript
// BAD: Tests implementation details
test("checkout calls paymentService.process", async () => {
  const mockPayment = jest.mock(paymentService);
  await checkout(cart, payment);
  expect(mockPayment.process).toHaveBeenCalledWith(cart.total);
});
```

Red flags:

- Mocking internal collaborators
- Testing private methods
- Asserting incidental call counts/order; query budgets and required lock/event order are valid contractual checks
- Test breaks when refactoring without behavior change
- Test name describes HOW not WHAT
- Inspecting irrelevant internals instead of the contract; database state/policies are valid evidence when PostgreSQL semantics are the contract

```typescript
// BAD for a retrievability-only contract: inspects storage instead of the read API
test("createUser saves to database", async () => {
  await createUser({ name: "Alice" });
  const row = await db.query("SELECT * FROM users WHERE name = ?", ["Alice"]);
  expect(row).toBeDefined();
});

// GOOD: Verifies through interface
test("createUser makes user retrievable", async () => {
  const user = await createUser({ name: "Alice" });
  const retrieved = await getUser(user.id);
  expect(retrieved.name).toBe("Alice");
});
```

## PostgreSQL contract checks

The storage example above does not prohibit database assertions. For RLS,
constraints, CAS, row/advisory locking, migrations, indexes, Outbox atomicity,
isolation and concurrency, use real PostgreSQL/PostGIS in the project Docker
workflow. Query-count assertions also test an explicit performance contract.

For example, an Outbox rollback regression should issue a real service command
inside a transaction, force that transaction to roll back, and query both the
aggregate and Outbox tables to prove neither change committed. Both assertions
belong to one atomicity behavior. Exercise the real service/repository path and
actual PostgreSQL rollback; a fake event collector does not prove this guarantee.
Use the existing domain fixtures and tenant/RLS scope, not a generic unscoped test.

**Tautological tests**: Expected value restates the implementation, so the test passes by construction.

```typescript
// BAD: Expected value is recomputed the way the code computes it
test("calculateTotal sums line items", () => {
  const items = [{ price: 10 }, { price: 5 }];
  const expected = items.reduce((sum, i) => sum + i.price, 0);
  expect(calculateTotal(items)).toBe(expected);
});

// GOOD: Expected value is an independent, known literal
test("calculateTotal sums line items", () => {
  expect(calculateTotal([{ price: 10 }, { price: 5 }])).toBe(15);
});
```
