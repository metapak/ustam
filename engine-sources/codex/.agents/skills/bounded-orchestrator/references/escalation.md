
# Escalation and stopping rules

Escalation responds to a named blocker, not task size.

## Retry ladder

1. First failure: inspect evidence and correct the contract, assumptions, or environment.
2. Second attempt: retry only with new evidence, narrower scope, or a changed method.
3. Second failure: stop repeating. Choose one:
   - narrow the objective
   - split ownership
   - ask the user for a missing decision
   - escalate one evidence-backed blocker to `failure_analyst`
   - escalate one high-risk decision to `advisor`
   - report blocked

## Luna to Terra

If `fast_lookup` cannot answer an exact query without interpretation, do not retry it broadly. Route to `explorer` or `researcher` with a new contract.

## Terra to Sol

Use `failure_analyst` only when the owner supplies:

- files and symbols already inspected
- observed evidence and exact failures
- hypotheses already tested
- one causal question still open

Do not ask Sol to repeat a generic repository scan.

## Astra advisor

Use `advisor` only for material architecture, security, privacy, billing, concurrency, or data-integrity consequences. It advises; the root still decides.

## Budget exhaustion report

```text
Status: BLOCKED
Candidate or commit:
Completed:
Failing evidence:
Attempts:
Unresolved decision:
Safest next action:
```

A truthful blocked result is better than an open-ended review loop that keeps moving the target.
