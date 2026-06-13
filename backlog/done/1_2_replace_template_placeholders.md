# 1_2 - Replace template placeholders

**Epic:** Foundation
**Estimated effort:** ~30 min
**State:** done

## Goal

Remove dangling template placeholders so the project contract can be followed
without ambiguity.

## Spec

Search the operating docs and backlog files for placeholder tokens such as
`<PROJECT_NAME>`, `<RUN_ENV>`, `<DOC_LANGUAGE>`, and `<DEFAULT_BRANCH>`. Replace
or delete every placeholder that applies to Lucy.

## Files to create/modify

- `agents.md` - placeholder cleanup
- `backlog/agent_index.md` - placeholder cleanup
- `backlog/_TEMPLATE.md` - placeholder cleanup

## Definition of Done

- [x] `rg "<[A-Z_]+>" agents.md backlog` returns no unresolved project placeholders.
- [x] Runtime, commands, docs language, and branch are concrete.
- [x] Targeted tests green in local runtime; Docker daemon unavailable in this session.
- [x] Post-task audit done

## Improvements noted

- Docker Compose verification should be rerun once the Docker daemon is active.
