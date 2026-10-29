# Adjustment review (run a2ff6789b7a2)

## JE-001 - PASSED_PENDING_APPROVAL
All checks passed. Awaiting human approval.

## JE-002 - REJECTED
The journal entry JE-002 was rejected because the total debits of 28,500.00 do not equal the total credits of 25,000.00, leaving an imbalance of 3,500.00. The system requires every entry to balance to zero. You should review each line amount in the entry to find where the 3,500.00 discrepancy originates and adjust the debits or credits so they match.

## JE-003 - QUARANTINED
The entry was quarantined because the booked FX revaluation of 11,200.0 for EUR cash account 1110 does not match the expected revaluation of 10,730.200 derived from the EUR balance of 825,400 and the period‑end rate of 1.095, leaving a difference of 469.800. Check whether the revaluation used the correct period‑end rate, the correct EUR cash balance, or the proper calculation method (e.g., using average vs. ending rate). Verify the source of the 1.095 rate and confirm the EUR balance at period‑end. Ensure the revaluation logic aligns with policy before releasing the entry.

## JE-004 - PASSED_PENDING_APPROVAL
All checks passed. Awaiting human approval.

## JE-005 - QUARANTINED
The entry was quarantined because line 1 uses account 6315, which does not exist in the current chart of accounts.  
Check whether account 6315 should be added to the COA or if the line should be recoded to a valid account.  
Also verify that the reclassification description matches the intended account change.

## JE-006 - PASSED_PENDING_APPROVAL
All checks passed. Awaiting human approval.

## JE-007 - PASSED_PENDING_APPROVAL
All checks passed. Awaiting human approval.

## JE-008 - REJECTED
The entry was rejected because it contains equal debits and credits on account 2170, resulting in a net zero effect with no accounting impact. This defect indicates the entry does not change any account balances. Check whether the intercompany settlement should affect account 2170 or another account, and verify the debit and credit amounts are correct.

## JE-009 - PASSED_PENDING_APPROVAL
All checks passed. Awaiting human approval.

## JE-010 - PASSED_PENDING_APPROVAL
All checks passed. Awaiting human approval.
