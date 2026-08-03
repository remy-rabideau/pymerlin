# Publishing to PyPI

## Prerequisites

- **`build`** — `pip install build`
- **`twine`** — `pip install twine`
- A `.pypirc` file with your PyPI API key.

## Steps

Run these commands from the `pymerlin/` package root (where `pyproject.toml` lives):

```shell
rm -rf dist
python3 -m build
python3 -m twine upload dist/*
```

This builds a source distribution and a wheel, then uploads both to PyPI.

:::{note}
The shim JAR (`pymerlin/_internal/jars/pymerlin-shim.jar`) is included in the wheel. If
you've rebuilt it from Java source (`./scripts/build-shim.sh`), make sure to commit the
updated JAR **before** publishing so the release ships the correct shim.
:::