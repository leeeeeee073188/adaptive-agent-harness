---
status: accepted
---

# Keep Harness policy independent of runtime foundations

The Agent Harness is the product boundary and owns composition, task lifecycle, policy, evidence, evaluation, and evolution. DeerFlow is the first Runtime Foundation behind a conformance-tested Runtime Adapter because inheriting its mature execution capabilities is valuable, while making DeerFlow middleware the architecture would prevent the Harness from being independently testable and replaceable.

## Consequences

Harness Core cannot import DeerFlow. Fake and DeerFlow adapters must satisfy the same behavioural conformance suite, and provider-specific task semantics remain in integrations.
