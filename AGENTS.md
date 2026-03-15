# AGENTS.md

## Big picture

- This repo runs a home-observability stack.

## AI Agent Interaction Rules

- Never execute existing repo scripts.
- Verification must use a standalone, temporary smoke-test file — separate from production code.
- Smoke-tests must be non-destructive, idempotent, read-only, and local.
