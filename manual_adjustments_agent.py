"""
Manual Adjustments Agent (prototype slice)

Flow: load -> deterministic checks per entry -> status -> LLM explanation -> write /output
Rule: code decides status and numbers. The LLM only explains, using the evidence we give it.
"""
import csv
import hashlib
import json
import os
import re
import time
from datetime import date
from decimal import Decimal
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("OPENROUTER_API_KEY")
MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"

DATA = Path("inputs")
OUT = Path("output")
PERIOD_START, PERIOD_END = "2024-10-01", "2024-12-31"  # ISO strings compare correctly
COA_CODE_COL = "account_code"

PASSED, QUARANTINED, REJECTED = "PASSED_PENDING_APPROVAL", "QUARANTINED", "REJECTED"


# ---------- 1. Loading (code) ----------
def load_coa() -> set:
    """Return the set of account codes (strings) in the COA."""
    with open(DATA / "chart_of_accounts.csv", newline="") as f:
        return {row[COA_CODE_COL].strip() for row in csv.DictReader(f)}


def load_entries() -> list:
    with open(DATA / "manual_adjustments.json") as f:
        return json.load(f)["entries"]


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def D(x) -> Decimal:
    """Always use Decimal for money. Convert via str to avoid float noise."""
    return Decimal(str(x))


# ---------- 2. Deterministic checks ----------
# Each check returns a list of defect dicts: {"code", "severity", "detail", "evidence"}
# severity is "reject" or "quarantine". No check may call the LLM.

def check_schema(entry: dict) -> list:
    """Validate required keys, line structure, and finite non-negative amounts."""
    defects = []
    if not isinstance(entry, dict):
        return [{
            "code": "INVALID_ENTRY",
            "severity": "reject",
            "detail": "Entry must be a JSON object.",
            "evidence": {"received_type": type(entry).__name__},
        }]

    for key in ("id", "date", "lines"):
        if key not in entry:
            defects.append({
                "code": "MISSING_ENTRY_FIELD",
                "severity": "reject",
                "detail": f"Entry is missing required field '{key}'.",
                "evidence": {"field": key},
            })

    entry_id = entry.get("id")
    if "id" in entry and (not isinstance(entry_id, str) or not entry_id.strip()):
        defects.append({
            "code": "INVALID_ENTRY_ID",
            "severity": "reject",
            "detail": "Entry id must be a non-empty string.",
            "evidence": {"id": entry_id},
        })

    entry_date = entry.get("date")
    if "date" in entry and (not isinstance(entry_date, str) or not entry_date.strip()):
        defects.append({
            "code": "INVALID_ENTRY_DATE",
            "severity": "reject",
            "detail": "Entry date must be a non-empty string.",
            "evidence": {"date": entry_date},
        })

    lines = entry.get("lines")
    if "lines" not in entry:
        return defects
    if not isinstance(lines, list):
        defects.append({
            "code": "INVALID_LINES",
            "severity": "reject",
            "detail": "Entry lines must be a list.",
            "evidence": {"received_type": type(lines).__name__},
        })
        return defects
    if len(lines) < 2:
        defects.append({
            "code": "INSUFFICIENT_LINES",
            "severity": "reject",
            "detail": "Journal entry must contain at least two lines.",
            "evidence": {"line_count": len(lines)},
        })

    for line_number, line in enumerate(lines, start=1):
        if not isinstance(line, dict):
            defects.append({
                "code": "INVALID_LINE",
                "severity": "reject",
                "detail": f"Line {line_number} must be a JSON object.",
                "evidence": {"line_number": line_number, "received_type": type(line).__name__},
            })
            continue

        for key in ("account", "debit", "credit"):
            if key not in line:
                defects.append({
                    "code": "MISSING_LINE_FIELD",
                    "severity": "reject",
                    "detail": f"Line {line_number} is missing required field '{key}'.",
                    "evidence": {"line_number": line_number, "field": key},
                })

        account = line.get("account")
        if "account" in line and (not isinstance(account, str) or not account.strip()):
            defects.append({
                "code": "INVALID_LINE_ACCOUNT",
                "severity": "reject",
                "detail": f"Line {line_number} account must be a non-empty string.",
                "evidence": {"line_number": line_number, "account": account},
            })

        amounts = {}
        for key in ("debit", "credit"):
            if key not in line:
                continue
            value = line[key]
            try:
                if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
                    raise ValueError
                amount = D(value)
                if not amount.is_finite() or amount < 0:
                    raise ValueError
            except (ValueError, TypeError, ArithmeticError):
                defects.append({
                    "code": "INVALID_LINE_AMOUNT",
                    "severity": "reject",
                    "detail": f"Line {line_number} {key} must be a finite numeric amount greater than or equal to zero.",
                    "evidence": {"line_number": line_number, key: value},
                })
                continue
            amounts[key] = amount

        if amounts.get("debit", D(0)) > 0 and amounts.get("credit", D(0)) > 0:
            defects.append({
                "code": "LINE_HAS_DEBIT_AND_CREDIT",
                "severity": "reject",
                "detail": f"Line {line_number} cannot contain both a debit and a credit.",
                "evidence": {
                    "line_number": line_number,
                    "debit": str(amounts["debit"]),
                    "credit": str(amounts["credit"]),
                },
            })

    return defects

def check_accounts(entry: dict, coa: set) -> list:
    """Every line account must exist in the COA."""
    defects = []
    for line_number, line in enumerate(entry["lines"], start=1):
        account = line["account"].strip()
        if account not in coa:
            defects.append({
                "code": "ACCOUNT_NOT_IN_COA",
                "severity": "quarantine",
                "detail": f"Line {line_number} references account {account}, which is not in the chart of accounts.",
                "evidence": {"account": account, "line_number": line_number},
            })
    return defects


def check_balance(entry: dict) -> list:
    """Debit and credit totals must match exactly using Decimal arithmetic."""
    debit_total = sum((D(line["debit"]) for line in entry["lines"]), D(0))
    credit_total = sum((D(line["credit"]) for line in entry["lines"]), D(0))
    if debit_total == credit_total:
        return []
    gap = debit_total - credit_total
    return [{
        "code": "ENTRY_OUT_OF_BALANCE",
        "severity": "reject",
        "detail": (
            f"Entry is out of balance."
        ),
        "evidence": {
            "debit_total": str(debit_total),
            "credit_total": str(credit_total),
            "difference": str(gap),
        },
    }]


def check_circular(entry: dict) -> list:
    """Reject an entry that nets to zero on every account."""
    account_net = {}
    for line in entry["lines"]:
        account = line["account"].strip()
        account_net[account] = account_net.get(account, D(0)) + D(line["debit"]) - D(line["credit"])
    if account_net and all(net == 0 for net in account_net.values()):
        return [{
            "code": "ENTRY_NETS_TO_ZERO_PER_ACCOUNT",
            "severity": "reject",
            "detail": "Entry has equal debits and credits on every account and has no net accounting effect.",
            "evidence": {"account_net": {account: str(net) for account, net in sorted(account_net.items())}},
        }]
    return []


def check_date(entry: dict) -> list:
    """Entry date must be a valid ISO date within the configured reporting period."""
    entry_date = entry["date"]
    try:
        parsed_date = date.fromisoformat(entry_date)
    except (TypeError, ValueError):
        return [{
            "code": "INVALID_ENTRY_DATE",
            "severity": "reject",
            "detail": f"Entry date {entry_date!r} is not a valid ISO date (YYYY-MM-DD).",
            "evidence": {"date": entry_date},
        }]

    start_date = date.fromisoformat(PERIOD_START)
    end_date = date.fromisoformat(PERIOD_END)
    if not start_date <= parsed_date <= end_date:
        return [{
            "code": "DATE_OUTSIDE_PERIOD",
            "severity": "reject",
            "detail": f"Entry date {entry_date} is outside the reporting period {PERIOD_START} through {PERIOD_END}.",
            "evidence": {
                "date": entry_date,
                "period_start": PERIOD_START,
                "period_end": PERIOD_END,
            },
        }]
    return []


def check_duplicate_id(entry: dict, seen_ids: set) -> list:
    """Quarantine repeated entry IDs and record each ID for later entries."""
    entry_id = entry["id"]
    if entry_id in seen_ids:
        return [{
            "code": "DUPLICATE_ENTRY_ID",
            "severity": "quarantine",
            "detail": f"Entry id {entry_id} has already appeared in this batch.",
            "evidence": {"entry_id": entry_id},
        }]
    seen_ids.add(entry_id)
    return []


def check_fx_reval(entry: dict) -> list:
    """Recompute EUR cash revaluation using the TB and configured FX rates."""
    account = entry["lines"][0]["account"].strip() if entry["lines"] else None
    fx_rates = {}
    with open(DATA / "fx_rates.csv", newline="") as f:
        for row in csv.DictReader(f):
            if row["currency"] == "EUR" and row["period"] == "2024-Q4":
                fx_rates[row["rate_type"]] = D(row["rate"])

    required_rates = ("period_average", "period_end")
    missing_rates = [rate_type for rate_type in required_rates if rate_type not in fx_rates]
    if missing_rates:
        return [{
            "code": "FX_REVALUATION_RATE_MISSING",
            "severity": "quarantine",
            "detail": f"Cannot verify EUR cash revaluation because EUR FX rates are missing: {', '.join(missing_rates)}.",
            "evidence": {"currency": "EUR", "missing_rates": missing_rates},
        }]

    eur_balance = D(0)
    with open(DATA / "trial_balance.csv", newline="") as f:
        for row in csv.DictReader(f):
            if row["account_code"] == account and row["currency"] == "EUR":
                eur_balance += D(row["debit"]) - D(row["credit"])

    if eur_balance == 0:
        return [{
            "code": "FX_REVALUATION_BALANCE_MISSING",
            "severity": "quarantine",
            "detail": "Cannot verify EUR cash revaluation because no non-zero EUR cash balance was found for the specified account.",
            "evidence": {"account": account, "currency": "EUR", "balance": str(eur_balance)},
        }]

    expected_revaluation = eur_balance * (fx_rates["period_end"] - fx_rates["period_average"])
    booked_revaluation = sum(
        (D(line["debit"]) - D(line["credit"]) for line in entry["lines"] if line["account"] == account),
        D(0),
    )
    difference = booked_revaluation - expected_revaluation
    print(
        "EUR cash revaluation check: "
        f"EUR balance={eur_balance}, period_average={fx_rates['period_average']}, "
        f"period_end={fx_rates['period_end']}, expected={expected_revaluation}, "
        f"booked={booked_revaluation}"
    )
    if abs(difference) <= D("0.01"):
        return []
    return [{
        "code": "FX_REVALUATION_MISMATCH",
        "severity": "quarantine",
        "detail": (
            f"EUR cash revaluation books {booked_revaluation} for EUR cash revaluation, but the TB balance "
            f"and supplied rates imply {expected_revaluation}."
        ),
        "evidence": {
            "currency": "EUR",
            "account": account,
            "eur_balance": str(eur_balance),
            "period_average_rate": str(fx_rates["period_average"]),
            "period_end_rate": str(fx_rates["period_end"]),
            "expected_revaluation": str(expected_revaluation),
            "booked_revaluation": str(booked_revaluation),
            "difference": str(difference),
        },
    }]


def run_checks(entry: dict, coa: set, seen_ids: set) -> list:
    defects = []
    defects += check_schema(entry)
    if defects:  # malformed entry: later checks would crash
        return defects
    defects += check_accounts(entry, coa)
    defects += check_balance(entry)
    defects += check_circular(entry)
    defects += check_date(entry)
    defects += check_duplicate_id(entry, seen_ids)
    accounts = {l["account"].strip() for l in entry["lines"]}
    if {"1110", "7310"} <= accounts:   # entry looks like an FX reval
        defects += check_fx_reval(entry)
    return defects


# ---------- 3. Status (code) ----------
def assign_status(defects: list) -> str:
    """reject beats quarantine beats pass."""
    if any(d["severity"] == "reject" for d in defects):
        return REJECTED
    if any(d["severity"] == "quarantine" for d in defects):
        return QUARANTINED
    return PASSED


# ---------- 4. LLM explanation (advisory only) ----------
def template_explanation(entry: dict, defects: list) -> str:
    """Fallback if the LLM fails or its answer fails verification."""
    return f"{entry['id']}: " + "; ".join(d["detail"] for d in defects)


def numbers_in(text: str) -> set:
    numbers = set()
    for number in re.findall(r"\d[\d,]*\.?\d*", text):
        try:
            normalized = D(number.replace(",", "")).normalize()
        except ArithmeticError:
            continue
        numbers.add(format(normalized, "f"))
    return numbers


def verify_explanation(text: str, evidence: dict) -> bool:
    """Require every number in the explanation to be present in the supplied evidence."""
    quoted_numbers = numbers_in(text)
    evidence_numbers = numbers_in(json.dumps(evidence, default=str))
    return quoted_numbers.issubset(evidence_numbers)


def explain_with_llm(entry: dict, status: str, defects: list, max_retries: int = 2) -> str:
    evidence = {"entry_id": entry["id"], "description": entry["description"],
                "status": status, "defects": defects}
    prompt = (
        "You help a finance user. Explain in 2-4 plain sentences why this journal entry was "
        f"{status}, and suggest what to check. Use ONLY the facts below. Do not invent numbers, "
        "accounts or statuses. Suggestions are advice, not decisions.\n\n"
        + json.dumps(evidence, indent=2, default=str)
    )
    for attempt in range(max_retries + 1):
        try:
            r = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {API_KEY}",
                         "Content-Type": "application/json"},
                json={"model": MODEL,
                      "messages": [{"role": "user", "content": prompt}]},
                timeout=60,
            )
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"].strip()
            if text and verify_explanation(text, evidence):
                return text
        except Exception as e:  # free models rate-limit often; never crash the run
            print(f"  LLM attempt {attempt + 1} failed: {e}")
        time.sleep(2)
    return template_explanation(entry, defects)


# ---------- 5. Output ----------
def write_outputs(results: list, run_id: str) -> None:
    OUT.mkdir(exist_ok=True)
    (OUT / "decisions.json").write_text(json.dumps(
        {"run_id": run_id, "results": results}, indent=2, default=str))
    lines = [f"# Adjustment review (run {run_id})", ""]
    for r in results:
        lines.append(f"## {r['entry_id']} - {r['status']}")
        lines.append(r.get("explanation") or "All checks passed. Awaiting human approval.")
        lines.append("")
    (OUT / "report.md").write_text("\n".join(lines))


# ---------- 6. Main ----------
def main() -> None:
    coa = load_coa()
    entries = load_entries()
    # run_id = hash of inputs, so the same inputs give the same run (idempotency)
    run_id = hashlib.sha256("".join(
        file_hash(DATA / f) for f in ["chart_of_accounts.csv", "manual_adjustments.json"]
    ).encode()).hexdigest()[:12]

    seen_ids, results = set(), []
    for entry in entries:
        defects = run_checks(entry, coa, seen_ids)
        status = assign_status(defects)
        explanation = explain_with_llm(entry, status, defects) if defects else None
        results.append({"entry_id": entry["id"], "status": status,
                        "defects": defects, "explanation": explanation})
        print(f"{entry['id']}: {status} ({len(defects)} defects)")
    write_outputs(results, run_id)


if __name__ == "__main__":
    main()