# Governance

ETLIR currently follows a **maintainer-led** model, intended to grow into a small
maintainer council as contributors join.

## Roles

* **Contributors**: anyone who opens an issue, discussion, or pull request.
* **Plugin owners**: contributors responsible for a specific source adapter or target
  emitter (listed in `.github/CODEOWNERS`). They review changes to their plugin and keep
  its capability manifest accurate.
* **Maintainers**: review and merge changes, own the Canonical IR, contracts, release
  process, and evaluation protocol. Current maintainer:
  Shiva Kumar Reddy Carimireddy ([@shiva-carimireddy](https://github.com/shiva-carimireddy)).

## Becoming a plugin owner or maintainer

Contributors with a sustained record of quality contributions to a plugin may be invited
to own it. Plugin owners who have shown good judgment across the codebase may be
nominated as maintainers. Once there are three or more maintainers, nominations are
decided by lazy consensus among them; until then, by the current maintainer.

## Decisions

* Routine changes: lazy consensus on the pull request.
* Changes to the Canonical IR, plugin contracts, capability semantics, claims policy, or
  evaluation protocol: an Architecture Decision Record in `docs/decisions/`, open for
  comment for at least 7 days before acceptance.
* Once there are three or more maintainers, disagreements not settled by discussion are
  decided by a majority vote of maintainers.

## Neutrality

ETLIR does not favor any vendor's source or target. Plugins are accepted on technical
merit, test evidence, and license compatibility. A vendor may maintain plugins for its
own products; its claims follow the same [claims policy](docs/claims-policy.md) as any
other contribution.
