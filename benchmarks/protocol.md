# Evaluation protocol (DRAFT, not frozen)

This protocol must be frozen, tagged, and cited **before** final result collection.
Changes after freezing require a new protocol version.

## Case partitions

Each case is placed in exactly one highest partition reached, with the reason recorded:

1. `inventory-only`
2. `structurally-resolvable`
3. `emit-capable`
4. `locally-executable` (with controlled fixtures)
5. `behaviorally-comparable` (independent expectations exist)

Parse success, emission, execution, and output agreement are separate claims.

## Measures

Every rate is reported as numerator/denominator, per file and per workflow, with raw rows
kept in `results/<release>/`.

| Measure | Numerator / denominator |
|---|---|
| Parse rate | accepted XML files / eligible XML files attempted |
| Reference resolution | resolved required links / required links discovered (by type; missing companions reported separately) |
| Canonical coverage | in-scope constructs mapped to schema-valid canonical constructs / in-scope discovered constructs |
| Strict emission coverage | constructs emitted under a validated capability rule / in-scope discovered constructs |
| Workflow package completion | workflows with complete validated package / workflows eligible for the profile |
| Traceability coverage | emitted elements with resolvable source evidence / emitted elements |
| Execution success | completed runnable cases / cases predeclared runnable |
| Output agreement | cases meeting the comparison policy / executed cases with independent expectations |
| Reproducibility | matching logical artifacts across clean repeated runs / repeated runs |
| Stage time and memory | per file, per workflow, total, under the pinned environment |

## Comparison policy (to be fixed before freeze)

Row multiset vs ordered comparison; null equality; decimal precision/scale and rounding;
timestamp time zone and precision; string trimming and case; tolerance (default: exact).

## Expectations

Expected outputs are authored from the **specification of the fixture**, not from
emitter output, and are committed before the emitter version that is evaluated. Agreement
is reported as *specified-behavior agreement* (see [claims policy](../docs/claims-policy.md)).

## Mutation check

The comparator must detect deliberately injected semantic errors (e.g. flipped
predicate, dropped null handling, wrong join type). The detection rate is reported.

## Baseline

A direct XML-to-Spark baseline on the same subset and inputs is optional. Without it, no
claims of being faster, more accurate, or easier to debug than direct conversion may be
made.
