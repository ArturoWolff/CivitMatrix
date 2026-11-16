# Code Comments

## Comment review guide

The permanent rule lives in [AGENTS.md](../AGENTS.md#code-comment-voice). Use these examples and checks when applying it to new and completed source.

1. **Explain the code.** Preserve the reason, invariant, trade-off, units, lifetime, trust boundary or surprising failure case. A useful explanation with a punchline is ideal. Add brief conversational replies where they help a reader follow the reasoning; keep routine code readable.
2. **Make the playground positive.** Roast the assumption, race condition, compiler, or your own fictional developer persona. Playful made-up agent voices are welcome. Keep real collaborators respected; fictional blame must be recognizable as a joke rather than an allegation about an actual person.
3. **Keep facts accurate.** Actual incidents, benchmarks, time spent, authorship and defects need evidence. Clearly absurd exaggeration/personification is fine. A joke never establishes a performance result, permission, security guarantee or approval for a hack. Existing correctness and zero-debt requirements still apply.
4. **Apply it retroactively.** Improve existing comments and add missing explanations in code you inspect, including completed features. A focused comment-only pass is authorized by this rule. Preserve unrelated edits and behavior, update stale notes, and leave generated/vendor or immutable historical reference files untouched unless their owning contract authorizes changes. Larger remaining sweeps stay tracked.
5. **Review the result.** Check technical truth, useful coverage, friendly tone and valid language syntax. Humor should accompany understanding rather than bury it in repetitive chatter. For comment-only retrofits, compare executable structure/tokens where practical; do not claim runtime coverage from that check. Update comments when the code changes.

Example voices (adapt to the project's actual comment syntax):

```text
Maintainer: Recheck file identity before deleting; the path can be replaced.
Race-condition goblin: "But the filename matched!" Nice try, you little shit.

Future agent: Can we skip cleanup when this throws?
Maintainer: Fuck no. The error path owns resources too.
```

## Initial retroactive coverage

The 2026-11-15 initial pass adds useful notes and playful banter in these existing modules:

- [downloader.py](../src/civitmatrix/downloader.py): shared writer locks and admission-context cleanup.
- [indexer.py](../src/civitmatrix/indexer.py): symlink cycles, sibling-file pairing and installed-state requirements.
- [sm_sidecars.py](../src/civitmatrix/sm_sidecars.py): cached publisher identity and ambiguous credit.
- [version_prune.py](../src/civitmatrix/version_prune.py): replacement during hashing, deletion order and retained index ownership.
- [ui/server.py](../src/civitmatrix/ui/server.py): token checks, spawn admission and restricted replacement files.

Executable ASTs and non-comment tokens match the saved pre-pass source baselines, and all five modules compile. These are comment-only static checks, not a runtime-suite claim. Pre-existing uncommitted work is preserved. Apply the same review retroactively to further inspected code; this initial pass does not claim every source file has been swept.
