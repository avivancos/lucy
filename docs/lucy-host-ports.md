# Lucy Host Ports

Lucy binds dedicated host ports so the local stack does not collide with
other projects that use the common defaults (`8000`, `3000`, `5432`, `6379`).

| Service | Host port | Container port | Notes |
| --- | --- | --- | --- |
| `lucy-api` | **8010** | 8000 | Browser / curl entrypoint; OpenAPI at `/docs` |
| `lucy-dashboard` | **3010** | 3000 | `NEXT_PUBLIC_LUCY_API_URL=http://localhost:8010` |
| `postgres` | **5419** | 5432 | Host tools use `:5419`; containers use `postgres:5432` |
| `redis` | **6310** | 6379 | Host tools use `:6310`; containers use `redis:6379` |
| `lucy-media-gateway` | 8081 | 8081 | Unchanged (optional stub) |

Source of truth: `docker-compose.yml`. Contract assertions live in
`tests/test_infrastructure.py`.
