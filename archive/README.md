# Historical material

Everything under `archive/` is non-authoritative evidence: completed plans, dated verification,
rejected investigations, or retired implementations. It is preserved so maintainers can understand
why current constraints exist, not as instructions for current work.

Current authority:

- [`../AGENTS.md`](../AGENTS.md) routes contributor tasks.
- [`../docs/architecture.md`](../docs/architecture.md) records shared current contracts.
- [`../README.md`](../README.md) owns user instructions.
- [`../TODO.md`](../TODO.md) is the sole active backlog.
- Active topic and host guides live under [`../docs/`](../docs/).

Do not update historical test counts, versions, commands, or workspace observations to look current.
Add a dated evidence record instead when a new run needs to be retained.

`release-evidence/` holds results from an **executed artifact**. A record there must identify the
build revision and manifest version it came from, and must not describe a source-tree run: the fields
each record carries, and what does not count as evidence, are in
[`../docs/release.md`](../docs/release.md).
