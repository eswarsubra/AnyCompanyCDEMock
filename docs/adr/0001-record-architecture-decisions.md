# 1. Record architecture decisions

- **Status:** Accepted
- **Date:** 2026-07-31

## Context

This is an evaluated builder project. Evaluators assess *why* decisions were made,
not only the final result. We need a lightweight, durable way to capture significant
technical decisions as they happen so the customer team can understand and extend the
solution after handoff.

## Decision

We will use Architecture Decision Records (ADRs), one Markdown file per decision in
`docs/adr/`, numbered sequentially. Each ADR records the context, the decision, and its
consequences. ADRs are written when a decision is made — not reconstructed at the end.

## Consequences

- The commit history and `docs/adr/` together tell the story of how the solution was built.
- New significant decisions (architecture, IaC tooling, IAM scope, region choices) each get an ADR.
- Superseded decisions are kept and marked, not deleted, preserving the reasoning trail.
