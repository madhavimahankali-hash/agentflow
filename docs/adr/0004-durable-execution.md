# 0004: Checkpoints, idempotency keys and an append-only history

**Status:** accepted

## Context
A run can wait hours for an approval. The server may restart in between. Network calls fail and get retried. Auditors want to know who did what, and why.

## Decision
- **Checkpoints:** LangGraph saves the full state after every node (`SqliteSaver` locally; Postgres or DynamoDB in production). A decision resumes the run from the checkpoint, even in a new process (`test_run_survives_restart`).
- **Idempotency:** every write uses the key `run_id:step_id`. If a step is replayed, the stored result is returned and the system isn't written twice.
- **History:** an append-only event log records each plan, delegation, tool call (inputs, outputs, latency), approval request and decision, with the approver's name and comment.
- **Approval races:** a decision is accepted only if the approval is still `pending`; a second decision gets HTTP 409.

## Consequences
- Long waits and restarts are safe, and every run can be audited end to end.
- SQLite is fine for one machine. For AgentCore Runtime or several API replicas, the checkpointer and history must move to a shared database.
