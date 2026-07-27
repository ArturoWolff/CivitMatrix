# Contributing

Thanks for helping CivitMatrix grow.

## Dev setup

```bash
git clone https://github.com/ArturoWolff/CivitMatrix.git
cd civitmatrix
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\Activate.ps1
pip install -e .
cp .env.example .env        # use a throwaway key for tests
python -m civitmatrix --dry-run --limit 3
```

## Guidelines

- Keep the tool **cross-platform** (pathlib, no OS-specific Python).
- Do not commit secrets, logs, or downloaded weights.
- Prefer small PRs aligned with [ROADMAP.md](ROADMAP.md).
- SM sidecar shape is a compatibility contract — change it carefully.
- **Ship docs with features:** update `README.md`, `docs/GUIDE.md`, and `ROADMAP.md` in the same change (or immediately after) so GitHub stays current.

## Code of conduct

Be respectful. No harassment. This project may be used with mature-content APIs — keep discussions technical and legal.
