# 0005: Every LLM call has a deterministic fallback

**Status:** accepted

## Context
The project must be easy to run for reviewers without AWS access, testable in CI without cost or flakiness, and still use Amazon Bedrock for real reasoning when it's available.

## Decision
`LLM_PROVIDER` selects the mode:

| Concern | `bedrock` | `fake` (offline) |
| --- | --- | --- |
| Planning | Structured-output plan from the tool catalog; extracts inputs from free text | Known workflow template matched by keywords |
| Research / analysis | Tool-calling loop over the agent's own tools, writes an assessment | Runs the planned tool directly |
| Final summary | Written by the model | Template from step notes |

In Bedrock mode the deterministic path is still the backbone: an invalid LLM plan falls back to the template, and the planned tool always runs even if the model skips it.

## Consequences
- `pytest` and the eval suite run in seconds in CI with no credentials.
- The Bedrock code paths are covered with a scripted stand-in model (`tests/test_llm_paths.py`). Real model quality is measured with `LLM_PROVIDER=bedrock python -m eval.run_eval`.
- Offline mode handles only known workflows. Free-form requests need Bedrock.
