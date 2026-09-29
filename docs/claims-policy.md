# Claims policy

ETLIR is research software that others will cite. Code, documentation, release notes,
PRs, and papers derived from this repository use the following vocabulary.

| Term | Meaning | Do not use it to mean |
|---|---|---|
| **Canonical IR** | The typed, versioned, source/target-independent model defined in `schemas/canonical/` | A universal ETL semantics |
| **Source trace / provenance** | Link from an entity or artifact to its source location and rule | Data lineage |
| **Data lineage** | Column/dataset dependency through transformations | Provenance |
| **Supported** | Declared `supported` in the emitter manifest **and** covered by tests | "Recognized by the parser" |
| **Constrained** | Supported only when stated, machine-checked preconditions hold | Supported in general |
| **Approximated** | Emitted with a documented semantic difference | Equivalent |
| **Blocked** | Not emitted; the affected path cannot run | Silently skipped |
| **Specified-behavior agreement** | Target output matches independently authored expected output under a declared comparison policy | Equivalence with the source platform |
| **Source equivalence** (e.g. PowerCenter equivalence) | Measured agreement with authorized executions of the source platform on the same inputs | Anything inferred from XML alone |
| **Lossless** (Raw IR) | Proven by a defined preservation or round-trip test | "We kept most attributes" |
| **Translation** | The end-to-end ETLIR process | Successful deployment ("migration") |

Prohibited without the stated evidence:

* "fully converted", "automatic migration", "100% coverage": never used.
* "scalable": only with a defined scaling experiment.
* "production-ready": only with documented deployment evidence.
* Multi-source or multi-target **portability**: only after a second, independently
  implemented adapter or emitter has been evaluated. Interface test doubles do not count.
* Percentages: always with numerator and denominator.
