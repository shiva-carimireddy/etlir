# Support matrix

Generated from the installed target emitters' capability manifests with
`etlir capabilities --markdown`. A test fails if this page and the manifests disagree.

A construct not listed for a target is **blocked**: nothing that depends on it is emitted
or run. (c) = constrained: supported only when the listed preconditions hold (see
`etlir capabilities <target>`).

Source coverage is separate: the PowerCenter adapter maps only the subset described in
[sources/powercenter.md](sources/powercenter.md); everything else becomes an explicit
unsupported operation, task or opaque expression, which blocks the affected path.

| Construct | duckdb | spark |
|---|---|---|
| `cast.bigint` | supported | supported |
| `cast.boolean` | supported | supported |
| `cast.decimal` | supported | supported |
| `cast.double` | supported | supported |
| `cast.integer` | supported | supported |
| `dependency.completion` | supported | supported |
| `dependency.failure` | supported | supported |
| `dependency.success` | supported | supported |
| `function.abs` | supported | supported |
| `function.add` | supported | supported |
| `function.and` | supported | supported |
| `function.avg` | supported | supported |
| `function.coalesce` | supported | supported |
| `function.concat` | supported | supported |
| `function.count` | supported | supported |
| `function.count_all` | supported | supported |
| `function.divide` | supported | supported |
| `function.eq` | supported | supported |
| `function.ge` | supported | supported |
| `function.gt` | supported | supported |
| `function.if` | supported | supported |
| `function.is_null` | supported | supported |
| `function.le` | supported | supported |
| `function.length` | supported | supported |
| `function.lower` | supported | supported |
| `function.lt` | supported | supported |
| `function.ltrim` | supported | supported |
| `function.max` | supported | supported |
| `function.min` | supported | supported |
| `function.multiply` | supported | supported |
| `function.ne` | supported | supported |
| `function.negate` | supported | supported |
| `function.not` | supported | supported |
| `function.or` | supported | supported |
| `function.rtrim` | supported | supported |
| `function.substr` | supported | supported |
| `function.subtract` | supported | supported |
| `function.sum` | supported | supported |
| `function.upper` | supported | supported |
| `lookup.all` | supported | supported |
| `lookup.any` | supported | supported |
| `lookup.error` | supported | supported |
| `operation.aggregate` | supported | supported |
| `operation.derive` | supported | supported |
| `operation.filter` | supported | supported |
| `operation.join` | supported | supported |
| `operation.lookup` | supported | supported |
| `operation.project` | supported | supported |
| `operation.read` | constrained (c) | constrained (c) |
| `operation.route` | supported | supported |
| `operation.write` | constrained (c) | constrained (c) |
| `task.dataflow` | supported | supported |
| `write.append` | supported | supported |
| `write.error_if_exists` | supported | supported |
| `write.overwrite` | supported | supported |
