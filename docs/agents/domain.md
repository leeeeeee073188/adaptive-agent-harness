# Domain Docs

This repository uses a single-context domain layout.

## Before exploring

Read when present:

- `CONTEXT.md`
- Relevant ADRs under `docs/adr/`

If these files do not exist, proceed silently. Domain-modeling skills create them lazily when terminology or architectural decisions are resolved.

## Layout

```text
/
├── CONTEXT.md
├── docs/
│   ├── agents/
│   └── adr/
└── src/
```

## Vocabulary

Use terms defined in `CONTEXT.md` consistently in issues, tests, proposals, and implementation. Avoid introducing synonyms for established concepts.

## ADR conflicts

If proposed work contradicts an existing ADR, report the conflict explicitly rather than silently overriding it.
