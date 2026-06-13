# 5 - Scaffold Next.js dashboard

**Epic:** Dashboard
**Estimated effort:** ~4 h
**State:** done

## Goal

Create the first Lucy Ops Command Center dashboard shell.

## Spec

The dashboard uses Next.js App Router, React, TypeScript, and a typed API
boundary. The first screen shows cost per minute, latency, sentiment, funnel
stage, live call state, RAG inspector, voice naturalizer, and MCP tools.

## Files to create/modify

- `dashboard/package.json` - frontend dependencies and scripts
- `dashboard/app/` - dashboard app shell
- `dashboard/components/` - reusable UI pieces
- `dashboard/lib/api.ts` - typed API boundary

## Definition of Done

- [x] Production build passes.
- [x] Npm audit has zero moderate-or-higher vulnerabilities.
- [x] First screen is responsive and operational, not marketing-style.
- [x] Targeted tests green in local runtime; Docker daemon unavailable in this session.
- [x] Post-task audit done

## Improvements noted

- Add generated OpenAPI client in follow-up `5_4`.
- Screenshot capture through the in-app browser timed out intermittently; DOM
  audit passed all required surface checks.
