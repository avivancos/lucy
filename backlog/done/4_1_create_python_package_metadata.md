# 4_1 - Create Python package metadata

**Epic:** Backend
**Estimated effort:** ~45 min
**State:** done

## Goal

Make Lucy installable and testable as a Python package.

## Spec

`pyproject.toml` defines package metadata, Python version support, runtime
dependencies, dev dependencies, setuptools package discovery, and pytest config.

## Files to create/modify

- `pyproject.toml` - package metadata and test configuration
- `src/lucy/__init__.py` - package version

## Definition of Done

- [x] Package installs in editable mode.
- [x] Runtime dependencies include FastAPI, Pydantic, Uvicorn, and OpenTelemetry API.
- [x] Dev dependencies include pytest and httpx.
- [x] Targeted tests green in local runtime; Docker daemon unavailable in this session.
- [x] Post-task audit done

## Improvements noted

- Docker Compose verification should be rerun once the Docker daemon is active.
