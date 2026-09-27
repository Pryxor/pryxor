# Releasing the `pryxor` package

This is the checklist to publish the Python SDK to PyPI. Run every command from
the `sdk/` directory.

---

## One-time setup

Install the packaging tools:

```bash
pip install -r requirements-dev.txt
```

Configure credentials. The recommended way is an API token stored in
`~/.pypirc` (never commit it):

```ini
[distutils]
index-servers =
    pypi
    testpypi

[pypi]
username = __token__
password = pypi-AgEIcHlwaS5vcmc...   # your PyPI token

[testpypi]
repository = https://test.pypi.org/legacy/
username = __token__
password = pypi-AgEIcHlwaS5vcmc...   # your TestPyPI token
```

> Prefer environment variables in CI:
> `TWINE_USERNAME=__token__` and `TWINE_PASSWORD=<token>`.

---

## Before you release

- [ ] Bump `version` in `pyproject.toml` (and `__version__` in `src/pryxor/__init__.py`).
- [ ] URLs point at the real repository (`https://github.com/Pryxor/pryxor`).
- [ ] The test suite passes (from the repo root: `pytest tests/ -q`).
- [ ] `ruff check src` is clean.

---

## Build

```bash
# Clean previous artifacts
rm -rf dist build src/*.egg-info

# Build the wheel and the sdist
python -m build
```

This produces:

```
dist/pryxor-<version>-py3-none-any.whl
dist/pryxor-<version>.tar.gz
```

---

## Validate the artifacts

Always check the metadata before uploading — this catches most "oops" moments
(missing files, wrong dependencies, bad README):

```bash
python -m twine check dist/*
```

Inspect the contents (optional but useful):

```bash
# What's inside the wheel
python -c "import zipfile,glob; print('\n'.join(zipfile.ZipFile(glob.glob('dist/*.whl')[0]).namelist()))"
```

Sanity-check in a clean virtualenv before publishing:

```bash
python -m venv /tmp/pryxor-check
/tmp/pryxor-check/bin/pip install dist/*.whl   # on Windows: %TEMP%\pryxor-check\Scripts\pip
/tmp/pryxor-check/bin/python -c "import pryxor; print(pryxor.__version__)"
```

---

## Publish to TestPyPI first

```bash
python -m twine upload --repository testpypi dist/*
```

Then install from TestPyPI to confirm end to end:

```bash
pip install --index-url https://test.pypi.org/simple/ \
            --extra-index-url https://pypi.org/simple/ pryxor
```

---

## Publish to PyPI

```bash
python -m twine upload dist/*
```

The package is live at `https://pypi.org/project/pryxor/`.

> **Name availability.** If `pryxor` is already taken on PyPI, change
> `[project].name` in `pyproject.toml` (e.g. to `pryxor-sdk`). The **import
> name stays `pryxor`** — only the distribution name changes.

---

## After publishing

- [ ] Tag the release in git: `git tag sdk-v<version> && git push --tags`.
- [ ] Create a GitHub release pointing at that tag.
- [ ] **First release only:** if you are migrating off the previous
      `pryxor-client` distribution, publish a final `pryxor-client` release whose
      README points users to `pip install pryxor` (see the note in
      [`docs/sdk/python-sdk.md`](../docs/sdk/python-sdk.md)).

---

## Notes

- The **core** package depends only on `requests`. Framework support is opt-in
  via extras (`pryxor[langchain]`, `pryxor[crewai]`, `pryxor[openai-agents]`).
- Never commit `dist/`, `build/`, or `*.egg-info/` — they are git-ignored.
- Never commit tokens. Use `~/.pypirc` or CI secrets.