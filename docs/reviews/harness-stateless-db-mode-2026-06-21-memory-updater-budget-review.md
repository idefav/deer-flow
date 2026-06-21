# Review: Memory Updater Budgeted Snapshot

Date: 2026-06-21

## Scope

Reviewed the close-out change that adds `memory.max_update_context_tokens` and switches the Memory updater prompt from full memory JSON to a bounded hierarchical summary plus top-facts snapshot.

## Findings

- No blocking issues found in the implemented scope.
- Persisted DB memory remains complete; only the updater prompt payload is budgeted.
- The snapshot preserves `user.*` and `history.*` summaries as budget allows, then adds facts by confidence and recency until the configured budget is reached.
- The existing `memory.max_injection_tokens` behavior for normal prompt injection remains separate and unchanged.

## Residual Risk

- Very small updater budgets may truncate summaries before facts; operators should keep the default `12000` unless they have measured prompt pressure.
- The updater still depends on the model to merge summaries correctly; the budget only controls input size.

## Verification Reviewed

```text
Red: 2 failed, 1 warning in 0.40s
Focused updater tests: 2 passed, 1 warning in 0.25s
Prompt injection regression: 11 passed, 1 warning in 0.30s
```
