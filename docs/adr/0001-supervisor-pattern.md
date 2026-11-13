# 0001: Supervisor (hub-and-spoke) orchestration on LangGraph

**Status:** accepted

## Context
Workflows cross several departments and systems. We need agents that specialise, a way for them to share results, and a run that can be inspected and audited afterwards.

Options considered:
1. **One agent with every tool.** Simple, but a large tool list hurts tool selection, and one prompt has to cover every role and permission.
2. **Peer-to-peer agents** that message each other. Flexible, but control flow is hard to predict, debug or audit.
3. **Supervisor + specialists.** One agent owns the plan and delegates; specialists report back.

## Decision
Use a supervisor that plans and routes, with three specialists (Research, Data Analysis, Task Execution). Build it as an explicit LangGraph `StateGraph`. Hand-offs happen only through the supervisor and the typed shared state, never agent to agent.

The LLM decides *what* to do inside the plan; the graph fixes *which paths are allowed*.

## Consequences
- Every hand-off is a recorded event (`delegated`), so a run can be replayed from history.
- Specialists get small, focused tool sets and prompts, which improves accuracy and lets each use a different model.
- The supervisor adds a hop per step. Acceptable here; independent research steps could run in parallel later (LangGraph `Send`).
