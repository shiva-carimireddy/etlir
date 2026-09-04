# Terminology

The following vocabulary is normative for ETLIR documentation and public interfaces.

| Term | Meaning |
|---|---|
| **ETLIR** | The open-source framework and project. It is used as a proper project name. |
| **Source artifact** | A file or document expressed in a source platform or source format. |
| **Source Adapter** | A component that adapts one source format into a Pipeline Semantic Model. |
| **Pipeline Semantic Model** | A versioned, platform-neutral representation of declared pipeline behavior. |
| **Model Validation** | Structural and cross-reference validation of a model document. |
| **Target Adapter** | A component that translates a valid model into a target representation. |
| **Target artifact** | A generated file intended for inspection or use with a target platform. |
| **Fidelity Assessment** | Accounting of how source and model behavior was handled across translation. |
| **Translation record** | The evidence entry connecting one model operation to its target disposition. |
| **Diagnostic** | A structured issue describing invalid, unsupported, ambiguous, lossy, manual, or internal behavior. |
| **Migration Evidence Bundle** | Versioned JSON evidence and its Markdown rendering for a translation run. |

## Dispositions

| Disposition | Meaning |
|---|---|
| `preserved` | The adapter applied a declared direct or semantic translation rule. |
| `approximated` | The target representation differs in a known, bounded way. |
| `unsupported` | The target adapter cannot represent the operation. |
| `ambiguous` | Available metadata allows more than one materially different interpretation. |
| `manual` | A person must supply or approve part of the target behavior. |
| `blocked` | The operation was not translated because another blocking condition prevented generation. |

`Preserved` is an adapter claim backed by a stated rule. It is not a proof of runtime equivalence.

## Usage rules

- Always call the public neutral contract the **Pipeline Semantic Model**. It is versioned and
  intentionally scoped; its name does not claim universal authority over every pipeline system.
- Target-side components are **Target Adapters**, and their public operation is `translate`.
- Call the output a **Migration Evidence Bundle** because it contains structured evidence,
  provenance, artifacts, and diagnostics as well as readable Markdown.
- Parsing is an internal responsibility of a Source Adapter, not the name of the whole component.
