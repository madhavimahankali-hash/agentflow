# Architecture decision records

Short records of the important design choices: the context, the decision, and what it costs.

| # | Decision |
| --- | --- |
| [0001](0001-supervisor-pattern.md) | Supervisor (hub-and-spoke) orchestration on LangGraph |
| [0002](0002-policy-based-approvals.md) | Human approval decided by rules, not by the model |
| [0003](0003-read-write-separation.md) | Only one agent can change business systems |
| [0004](0004-durable-execution.md) | Checkpoints, idempotency keys and an append-only history |
| [0005](0005-offline-first-llm.md) | Every LLM call has a deterministic fallback |
