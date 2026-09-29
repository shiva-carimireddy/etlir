# ADR-0001: Apache-2.0 license with DCO contributions

**Status:** Accepted, 2026-09-28

## Context
ETLIR should be adoptable by individuals and companies, accept contributions from
vendors and competitors, and be citable research software.

## Decision
* License: **Apache-2.0**. It is permissive and business-friendly, includes an explicit
  patent grant and patent-retaliation clause (§3), and is the norm in the data ecosystem
  ETLIR targets (Spark, Airflow, Arrow, Iceberg). Copyleft (GPL/AGPL) was rejected
  because it deters adoption and embedding by companies. MIT was rejected because it has
  no patent grant.
* Contributions: **Developer Certificate of Origin** (`Signed-off-by`), inbound=outbound
  under Apache-2.0 §5. No CLA, which lowers the barrier for corporate contributors whose
  legal teams already accept DCO.
* `NOTICE` carries copyright and a trademark/non-affiliation statement.
* Third-party corpora are not redistributed unless their licenses permit it.

## Consequences
The project cannot later be relicensed as proprietary without every contributor's
consent. This is intended. Enforce DCO with the GitHub DCO app.
