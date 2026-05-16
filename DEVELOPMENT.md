# Development

This document is for maintainers of ecs-deployer-boto3.

## Releasing a new version

Releases are automated via GitHub Actions. To publish a new version to PyPI:

1. Update the `version` in `pyproject.toml`
2. Commit and push to `main`

The CI workflow will:
- Run lint
- Check if the version in `pyproject.toml` is higher than what's on PyPI
- If yes, build and publish the new version

No manual PyPI uploads or tags required. The version in `pyproject.toml` is the
single source of truth.

PyPI trusted publishing (OIDC) is configured on the `pypi` GitHub environment.

## Local development

Install dependencies:

```bash
uv sync --group dev
```

Run linting:

```bash
uv run ruff check
uv run ruff format --check
```

Format code:

```bash
uv run ruff format
```
