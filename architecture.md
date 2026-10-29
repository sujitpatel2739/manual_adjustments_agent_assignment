# Architecture: Agentic Financial Statement Generation

## 1. Problem decomposition
- **Ingestion:** Load `trial_balance.csv`, `chart_of_accounts.csv`, `prior_period_tb.csv`, `fx_rates.csv`, `manual_adjustments.json`. Keep every TB row as its own record (file, row no., file hash), grouped by (`account_code`, `currency`). Amounts are stored as Decimal. Raw rows are never overwritten.
- **Mapping:** Exact `account_code` match against COA only (confidence 1.0 or 0.0). No fuzzy matching in the prototype. Unmatched codes go to a human. A closest-match suggestion may be shown to the reviewer but is never auto-applied.
- **Adjustments:** Read `manual_adjustments.json`. Per entry and per line, deterministically check:
  - required fields, at least 2 lines, non-negative amounts
  - each account exists in COA
  - total debit = total credit (exact, to the cent)
  - date within period
  - not circular or net-zero per account
  - not a duplicate entry ID
  - reval entries recomputed from `fx_rates.csv` and compared
- **FX:** Look up rates by currency (period-end for BS, average for P&L). A missing rate is flagged, never estimated.
- **Statement build:** Pure arithmetic over adjusted, mapped, USD balances (out of prototype scope).
- **Validation:** Deterministic checks before output (see section 5).
- **Reporting:** Decision log in `/output` (entry ID, status, defect, evidence), plus a plain-English summary per non-passed entry.
- No silent plugging: every defect is flagged and logged, never fixed quietly.

**Entry flags**
- `PASSED_PENDING_APPROVAL`: all checks pass, awaiting human approval before posting.
- `APPROVED`: approved by a human, will be posted.
- `QUARANTINED`: arithmetic passes, but there is an unresolved ambiguity (FX, rename, duplicate, value mismatch), pending human review before posting.
- `REJECTED`: unbalanced, unknown account or no-op. Not postable until corrected and resubmitted under a new ID.

## 2. Agent topology
- Selected slice: **manual adjustments agent.**
- **Deterministic pipeline with one narrow LLM step.**
- Code does reading, validation, arithmetic and status assignment. The LLM only writes the plain-English explanation and suggests options, using the evidence code hands it.
- The LLM never computes, maps or sets a status.
- Pipeline: `Ingest → Map/Validate → Adjustment checks → Status → LLM explainer → Human review → Log`
#### Agent Flow Chart
![Agent Flow Chart](flowchart.png)
- Why not more agents: every sub-task is either exact (code) or a single text-generation task, so extra agents add cost and failure points without adding value.

## 3. Deterministic code vs. LLM
| Component | Code / LLM | Why |
|---|---|---|
| Parsing, typing, hashing | Code | Exact, reproducible |
| COA mapping (exact) | Code | Hallucination risk is unacceptable |
| Balance, account, date, circular checks | Code | Pass/fail must be reproducible |
| FX lookup and reval recompute | Code | Rates are facts, math must be exact |
| Status assignment | Code | Auditable rules |
| Rejection and quarantine explanation | LLM | Readable language for finance users |
| Suggested fixes and options | LLM | Points to evidences; helps reviewer; never auto-applied |

**Rule:** the LLM may explain and suggest, using only the evidence provided. It may not produce numbers, mappings, statuses or approvals.

## 4. Defects, detection and handling
| Defect | Detection | Status |
|---|---|---|
| Unmapped or hallucinated mapping | Exact code not in COA | REJECTED (entry lines) / QUARANTINED (TB account) |
| Debits ≠ credits | Sum check | REJECTED |
| Account not in COA | Lookup | REJECTED |
| Circular or net-zero IC entry | Net per account is zero | REJECTED (no-op), resubmit with real counter-account |
| FX gap or missing rate | Rate lookup fails | QUARANTINED |
| Reval mismatch | Recompute ≠ booked | QUARANTINED |
| Renamed account | Code in prior TB, not in COA | QUARANTINED |
| Duplicate account or entry | Same key twice | QUARANTINED |
- Every defect is deterministically classified, flagged and logged with evidence.

## 5. Validation and self-correction loop
- **Checks before output:** each entry passes the section 1 checks. The batch has no duplicate IDs, every line has a status, and the TB gap is reported.
- Before building statements: adjusted TB balances, A = L + E, net income ties to retained earnings, cash ties to the cash flow statement, equity ties to SOCIE, and no TB row gets silently dropped.
- **On failure:**
  - Deterministic checks are **Reproducible**. The entry is assigned a status, and the batch continues.
  - LLM explanation: validate output against evidence, retry max 2 times, then fall back with a message.
  - A batch-level failure (e.g. the TB doesn't balance) is reported with the gap and causes; it is never plugged. Statements are marked provisional and list what was excluded.
- Stop when the retry limit is reached or a blocking error occurs, then escalate to a human.

## 6. Traceability and audit
- **IDs:** source row = `file:row:hash`; entry line = `JE-id:line`; every decision = `run_id:entry_id`.
- **Lineage:** every statement number lists the input record IDs, mapping used, rate used (with source row) and adjustment lines applied.
- **Example, Allowance for doubtful accounts (1121):** BS cell (230,000) ← TB row for 1121 credit 185,000 + JE-004 line 2 credit 45,000 (status APPROVED) ← COA mapping 1121 (exact, v1).
- **Navigation:** cell → sources, and source row → every cell it affects.
- **Idempotency:** `run_id` = hash of inputs + COA version + rules version + config. Same inputs return the stored result. Entries are deduplicated by ID. LLM outputs are cached, not regenerated.
- **Versioning:** append-only log. A correction is a new entry (`JE-005-v2`). History is never edited. Human-approved decisions are stored and reused.

## 7. Human-in-the-loop
- **Escalation triggers:** any QUARANTINED or REJECTED entry, and any provisional batch.
- **Reviewer sees:** entry, failed check, evidence (numbers, accounts), statement impact if excluded, suggested options.
- **Reviewer can:** approve, reject, or correct and resubmit.
- **Records:** reviewer, timestamp, decision and reason, appended to the log.

## 8. Data defects found and assumptions
| Defect | Handling |
|---|---|
| TB debits ≠ credits (credits higher by 4,800) | Report the gap, don't plug, mark provisional |
| Duplicate `6310` rows (245,000 and 38,500) | Keep both rows, QUARANTINE, ask whether to merge |
| `9999` Suspense not in COA | Quarantine, show closest-match suggestion |
| JE-002 debits 28,500, credits 25,000 | REJECTED, 3,500 gap |
| JE-005 uses `6315`, not in COA | REJECTED |
| JE-008 debits and credits `2170` 320,000 (net zero) | REJECTED as no-op |
| JE-003 EUR reval 11,200 vs recomputed ~19,810 | QUARANTINED |
| GBP period-end rate missing | QUARANTINED, flag |
| Prior TB: `6905` renamed, no P&L, out of balance (~2.8M) | Quarantine rename, note comparatives limited |
| COA: 1150 and 2170 cash-flow category `TBD`, 1290 no children | Flag, no auto-assignment |

**Assumptions (pending reply to clarifying questions):**
- EUR/GBP rows are local currency, translated at the period-end rate.
- Prior TB is a closing balance sheet only, and `6905` maps to `6900`.
- Bad entries are quarantined or rejected individually; the rest of the batch continues.
- JE-003 is applied on top of the TB balances.
- Single entity.

## 9. Prototype scope (the slice)
- **Does:** reads the COA, TB and adjustments; validates all 10 entries; assigns a status; writes the decision log and a plain-English report with LLM explanations to `/output`.
- **Does not:** build statements, run FX translation of the whole TB, consolidate entities, or map TB accounts beyond exact match.


## 10. Limitations & Enhancements
- **At scale:** exact mapping won't cover thousands of accounts across ERPs. It needs a mapping layer with confidence scores and stored human approvals. Multi-entity consolidation needs IC matching and graph cycle detection. A file-based log needs a database. Asynchornus pipeline.
- **Enhancements:** statement builder, cumulative translation adjustment, full cash flow and SOCIE, role-based approvals, monitoring.