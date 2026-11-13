# 0002: Human approval decided by rules, not by the model

**Status:** accepted

## Context
Some actions are sensitive: saving bank details, large payments, emailing external parties. A human must approve them. The question is who decides that an action *is* sensitive.

If the LLM decides, a cleverly worded request or injected text can persuade it that approval isn't needed.

## Decision
`app/policy.py` assigns each action a tier using rules on the **actual arguments**:

- each tool has a base tier (`erp_set_bank_details` = high)
- rules can raise or lower it (payment above `PAYMENT_APPROVAL_THRESHOLD` → high; email to the internal domain → low)
- `APPROVAL_MODE=strict` also gates medium-risk actions, e.g. during an initial trust period

High-risk actions pause the graph with LangGraph `interrupt()`. The approver sees the tool, the arguments, the reason and the evidence gathered by the read agents, and can approve, reject or **edit the arguments**. Edited arguments are re-validated by the tool's Pydantic model.

## Consequences
- Approval behaviour is predictable, testable (`tests/test_policy_and_tools.py`) and explainable to auditors.
- Rules must be maintained as tools are added; a new write tool defaults to its declared base tier.
- A rejection currently stops the run. Re-planning after a rejection is on the roadmap.
