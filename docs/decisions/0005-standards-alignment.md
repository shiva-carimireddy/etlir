# ADR-0005: Align with existing standards; prioritize an executable second pair

**Status:** Proposed, 2026-09-28. Needs a maintainer decision before G2 expands beyond
the minimal slice.

## Context
Reviewers will ask why ETLIR invents its own relational algebra, lineage format, and SQL
parser when established open projects exist. They will also note that a single
source/target pair cannot demonstrate the decoupling claim, and that XML-only evidence
cannot show equivalence with the source platform.

## Proposal
1. **Substrait for the relational core (evaluate).** Keep ETLIR's envelope (workflow
   graph, parameters, bindings, provenance, capability states, evidence) as the
   contribution, and evaluate lowering canonical dataflows to or from
   [Substrait](https://substrait.io) plans. This avoids reinventing relational semantics
   and gives a path to engines with Substrait consumers.
2. **OpenLineage for lineage export.** Emit `LineageEdge`s as OpenLineage column-lineage
   facets, so evidence plugs into existing lineage tooling.
3. **SQLGlot for SQL overrides.** Parse and classify SQL overrides into an accepted
   subset with a maintained, dialect-aware parser instead of treating all SQL as opaque.
4. **Executable second source: Apache Hop / Pentaho Kettle XML.** Hop is open source and
   runs locally, which enables **measured source-vs-target agreement on identical
   inputs**. That is the evidence PowerCenter cannot provide publicly. Public samples
   are also more likely to be permissively licensed.
5. **Cheap second target: SQL (DuckDB).** Demonstrates "same Canonical IR, different
   emitter, adapter unchanged" with little effort, and serves as a differential oracle
   against Spark.
6. **Reference interpreter for Canonical IR.** A small, pure-Python interpreter gives the
   IR executable semantics. It is an oracle for emitters and makes the function
   catalog's null/decimal/time rules testable (including with property-based tests).

## Consequences
Items 2, 3, 5, and 6 are low-risk and strengthen the paper. Item 1 needs a spike before
it is committed to. Item 4 is the strongest evidence upgrade and is likely a second
paper.
