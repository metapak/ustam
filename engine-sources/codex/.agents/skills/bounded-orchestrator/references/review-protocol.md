
# Frozen candidate review protocol

## Inputs

The owner gives the Astra reviewer:

- user requirements and non-goals
- base commit or comparison point
- frozen candidate commit or fingerprint
- changed files or exact diff
- verification commands and results
- known residual risks

## Reviewer output

Each material finding uses:

```text
ID: R1
Severity: P0 | P1 | P2 | P3
Confidence: high | medium | low
Evidence: exact file, symbol, behavior, command, or reproduction
Impact: concrete failure mode
Smallest fix: bounded corrective action
Validation: proof that would close the finding
```

No style-only comments, speculative redesigns, unrelated pre-existing issues, implementation, or direct worker instructions.

A clean review returns:

```text
PASS
Residual uncertainty: <none or a concise limitation>
```

## Owner triage record

```text
Finding: R1
Decision: accept | reject | defer | needs evidence
Reason:
Repair owner and scope, when accepted:
Validation required:
```

P0 and high-confidence P1 findings block. P2 requires an explicit owner decision. P3 never triggers automatic repair. Low-confidence findings need evidence before blocking.

## Re-review

A re-review is narrow. It checks only whether accepted findings were fixed, whether the fix introduced a directly adjacent regression, and whether required validation now passes. It does not restart a broad search.
