# 0003: Only one agent can change business systems

**Status:** accepted

## Context
Research reads untrusted content: web pages, news, documents. Any of it can contain instructions aimed at the model (prompt injection). If the agent that reads that content can also write to the ERP, one poisoned page could trigger a payment.

## Decision
- Research and Data Analysis get **read-only** tools.
- Task Execution is the **only** agent with write tools (`writes=True`).
- Ownership is enforced in code twice: when the plan is validated, and at every tool call.
- Before any execution step, the supervisor checks the risk score and halts high-risk runs.

## Consequences
- Injected instructions can't reach a write tool: the read agents have none to call.
- The blast radius of a mistake is limited to one agent, which is also the one behind the approval gate.
- A few steps need an extra hand-off that a single all-powerful agent could do directly. That cost is worth the containment.
