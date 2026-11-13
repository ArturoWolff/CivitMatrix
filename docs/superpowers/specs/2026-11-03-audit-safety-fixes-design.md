# Audit safety fixes

The user authorized fixing all findings in the November 3 collection/code audit.
The audit supplies twelve confirmed bugs and fourteen reproductions at
`/run/media/arturo/Datos2/CivitMatrix/logs/audit-2026-11-03/`.

Preserve real model weights and every distinct version. No real model, sidecar,
preview, partial, or other existing library file may be deleted during this task
without separate explicit user authorization. Prepare a concrete deletion proposal
only after verifying identical bytes, model/version/file identity, author, base,
and format. Different releases (including v1 versus v2) never qualify as duplicates.
Unknown or conflicting identity means preserve. Workflow references matter when
selecting a proposed survivor. Never manufacture missing publisher facts.

Implement bounded fixes in the existing Python/stdlib architecture rather than
introducing a service or database. Keep historical versions by default; any
optional old-version pruning must be explicit, base-scoped, genuinely older,
and protect a committed surviving file. Hash skips and pending reservations must
never authorize pruning. Duplicate deletion requires actual payload equality and
complete matching identity. Heal must respect RemoteFileId and never silently
substitute another variant. Follow directory aliases once with cycle detection.

Make writer locks race-safe for identical and overlapping physical scopes.
Admit UI jobs before changing shared state, use immutable per-job manifests, and
retain restrictive secret-file permissions. Cache keys include all API filters.
Resumed responses must validate ranges and final lengths. Resolve filter values
with explicit CLI > manifest > preset > environment/default, preserving false,
zero, empty lists and explicit All. Validate safetensors dtype/shape byte lengths.

Regression tests must fail on the audited implementation and pass after fixes.
Use temporary fixtures and mocked network boundaries. Run the complete suite and
an independent final review; do not run destructive library maintenance.

After code verification, apply the tested changes to the user's original checkout
without overwriting unrelated edits. Recheck targeted library data using verified
local/cached sources and bounded remote lookups where useful. Record any data
that remains unknown. Present eligible duplicate bundles for authorization; do
not remove them during implementation or validation.
