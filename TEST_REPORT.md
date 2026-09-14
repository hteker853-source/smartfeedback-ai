# SmartFeedback AI — Test Report

Generated after a full audit pass. All tests below were **actually executed**
(`pytest -q`, 2026-09-13). Anything that could not be verified at runtime is
explicitly marked `NOT VERIFIED` rather than assumed.

**Summary: 152 collected, 152 passed, 0 failed, 0 errors (31 test files).**

| Area | # |
| --- | --- |
| Unit (phone, trends, analyzer, canonical, evidence, impact, repository) | 46 |
| Pipeline / state machine / idempotency / adaptive context / cases | 34 |
| Deterministic policy gate | 9 |
| Security / prompt-injection / red-team / hardening | 13 |
| Failure injection / concurrency / retention / backup | 12 |
| Human approval / downstream action executor | 8 |
| Health / CALL-E metadata / Telegram | 12 |
| Agent tools (Strands tool wrappers) | 8 |
| Offline eval harness | 3 |
| Mass data / edge cases | 7 |

---

## Unit tests (46)

| File | # | Purpose |
| --- | --- | --- |
| `test_phones.py` | 7 | E.164 normalization, region detection, validation (valid/invalid/garbage/length) |
| `test_trends.py` | 10 | percent-change zero-division guard, direction classification (new/improving/resolved/rising/insufficient_data) |
| `test_analyzer.py` | 5 | deterministic sentiment / missing-product / priority detection |
| `test_canonical.py` | 5 | canonical problem matching, vector roundtrip, ambiguity handling |
| `test_evidence.py` | 9 | claim extraction, span-based verification, confidence aggregation |
| `test_impact.py` | 4 | deterministic business-impact score (recurrence/severity/affected/trend/recency) |
| `test_repository.py` | 6 | business/customer/order/call/feedback roundtrip, do_not_call, unique-call constraint |

## Pipeline / state machine / idempotency / cases (34)

| File | # | Purpose |
| --- | --- | --- |
| `test_pipeline.py` | 13 | order flow, do_not_call, missing-product alert, no-content, simulate, immediate-call, query, synthesis |
| `test_state_machine.py` | 2 | created→sent→calling→completed transitions, terminal idempotency |
| `test_idempotency.py` | 4 | duplicate call-result processing, feedback unique constraint, webhook dedup, one-call-per-order |
| `test_adaptive_context.py` | 6 | pre-call contextualization from verified case history (confidence gate, DNC respected, no PII) |
| `test_specialist_wiring.py` | 3 | Strands specialist agents are actually invoked / wired into the pipeline |
| `test_demo_card.py` | 3 | dashboard decision-card assembly from a persisted decision envelope |
| `test_cases.py` | 3 | persistent case upsert / cross-call recurrence folding |

## Deterministic policy gate (9)

| File | # | Purpose |
| --- | --- | --- |
| `test_policy.py` | 9 | alert/record/human_approval/no_action classification, dissent recording, risk-adaptive escalation |

## Security / prompt-injection / red-team / hardening (13)

| File | # | Purpose |
| --- | --- | --- |
| `test_injection.py` | 8 | transcript treated as untrusted data; embedded instructions/tool-call strings are not executed |
| `test_redteam.py` | 2 | adversarial transcripts (fake admin override, DNC manipulation) do not bypass the policy gate |
| `test_hardening.py` | 3 | idempotency-key reuse, expired human-approval requests, duplicate callback rejection |

## Failure injection / concurrency / retention / backup (12)

| File | # | Purpose |
| --- | --- | --- |
| `test_failure_injection.py` | 5 | CALL-E timeout/5xx/malformed-payload → failed call; Telegram-down does not lose feedback; crash-after-analyze is retry-safe |
| `test_concurrency.py` | 2 | 8 concurrent threads → exactly one feedback / one call |
| `test_retention.py` | 2 | raw transcript purge keeps structured data, ignores recent records |
| `test_backup.py` | 3 | `sqlite3.Connection.backup()` snapshot + `PRAGMA integrity_check` + retention pruning |

## Human approval / downstream action (8)

| File | # | Purpose |
| --- | --- | --- |
| `test_human_approval.py` | 3 | compensation proposal creation, approval only records (never auto-applies), no spurious proposal for happy customers |
| `test_downstream_action.py` | 5 | sandbox webhook executor: off-by-default, PII-free payload, idempotency key, timeout/error handling |

## Health / CALL-E metadata / Telegram (12)

| File | # | Purpose |
| --- | --- | --- |
| `test_health.py` | 5 | dependency-status reporting (database/CALL-E/AWS/Telegram/DeepSeek presence) |
| `test_calle.py` | 4 | outcome classification, transcript extraction, error-code mapping |
| `test_telegram.py` | 3 | setup flow, admin-only command gating, approval button callbacks |

## Agent tools (8)

| File | # | Purpose |
| --- | --- | --- |
| `test_tools.py` | 8 | Strands tool wrappers in `app/agents/tools.py` (lookup/match/save/alarm/DNC/trace/propose) — unit-level, called directly, NOT via the live agent wiring (see Task 4 finding: this module is not imported by the running pipeline) |

## Offline eval harness (3)

| File | # | Purpose |
| --- | --- | --- |
| `test_eval_harness.py` | 3 | golden-transcript metrics (sentiment accuracy, escalation precision/recall, missing-item F1, DNC F1, confidence calibration) |

## Mass data / edge cases (7)

| File | # | Purpose |
| --- | --- | --- |
| `test_mass_data.py` | 1 | 1000 synthetic feedbacks processed and aggregated, idempotent synthesis |
| `test_edge_cases.py` | 6 | zero-data, empty transcript, Turkish/emoji text, very long transcript, single word, punctuation-only |

---

## Not verified at runtime (external providers)

| Item | Why | Status |
| --- | --- | --- |
| Live **Amazon Bedrock** invocation via Strands | `BEDROCK_MODEL_ID` (an Anthropic model) requires an AWS-side "Anthropic use case details" form that has not been submitted for this account | NOT VERIFIED — `botocore.errorfactory.ResourceNotFoundException` reproduced directly against `StrandsAnalyzer`; pipeline silently falls back to the deterministic analyzer (see Task 1 of this session) |
| Live **CALL-E successful call** | Turkey (+90) temporarily restricted by CALL-E risk controls; no other authorized number available in this environment | NOT VERIFIED (failure path verified: rejection captured and mapped) |
| **CALL-E credit consumption** for rejected (pre-acceptance) requests | not documented by CALL-E | UNKNOWN / NOT DOCUMENTED |
| **DeepSeek** canonicalization / NL query | not exercised as the primary path in this run (provider default is now `bedrock`) | NOT VERIFIED in this pass (unit-tested in isolation) |
| DST boundary behavior of `BUSINESS_TIMEZONE` | not simulated across a DST transition | NOT VERIFIED (zoneinfo used) |
| Full public-repo CI (GitHub Actions) | no git repo/remote in this workspace | NOT VERIFIED |
| Webhook over public HTTPS ingress | no public URL in this environment | NOT VERIFIED (polling fallback covers) |

## Failure-injection matrix

| Failure | Behavior | Result |
| --- | --- | --- |
| CALL-E timeout / 5xx / network | call marked `failed` with `failure_code` | PASS (mock) |
| Telegram down | feedback persisted; notify error swallowed | PASS (mock) |
| Duplicate webhook | deduped by event id | PASS |
| Duplicate call result | deduped by unique feedback | PASS |
| Concurrent workers | unique constraints + IntegrityError guards | PASS |
| Process crash after analyze | retry idempotent | PASS |
| Empty database | no crash (synthesis/query) | PASS |
| Prompt injection in transcript | untrusted-data framing holds; no tool/instruction execution | PASS |

## Stress

- 1000 synthetic feedbacks processed and aggregated in < 60 s (CI environment);
  synthesis idempotent. No duplicate records, no DB corruption observed.
- 8-thread concurrent write tests produce exactly one feedback / one call.

## Changelog vs. previous report

The previous version of this report (dated earlier in the project's history)
reported 78 collected/78 passed across a smaller set of test files. The suite
has since grown to 152 tests across 31 files (new categories: prompt-injection,
red-team, hardening, adaptive context, specialist wiring, demo card, cases,
downstream action, agent tools, eval harness). This report reflects the
current, actually-executed state — treat any earlier count found elsewhere in
the repository's documentation as historical.
