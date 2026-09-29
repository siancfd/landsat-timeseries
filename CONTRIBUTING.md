# Contributing

Issues and pull requests are welcome.

```bash
git clone <your-fork>
cd landsat-timeseries
pip install -e ".[dev]"
pytest -q
ruff check src tests
```

- Tests must not need network access: use the synthetic-scene helpers in `tests/test_core.py`.
- Keep changes focused; one topic per PR.
- New behaviour needs a test and a line in the README if it changes results.
- Never commit imagery, outputs or AOI boundary files (see `.gitignore`).
