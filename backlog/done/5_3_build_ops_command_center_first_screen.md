# 5_3 - Build Ops Command Center first screen

**Epic:** Dashboard
**Estimated effort:** ~2 h
**State:** done

## Goal

Implement the first dashboard screen as an operational command center.

## Spec

The dashboard displays cost per minute, latency p95, sentiment, funnel stage,
live call transcript, trace waterfall, RAG inspector, voice naturalizer, and MCP
tool status.

## Files to create/modify

- `dashboard/app/page.tsx` - dashboard screen
- `dashboard/components/MetricTile.tsx` - metric tile
- `dashboard/components/Waterfall.tsx` - latency waterfall
- `dashboard/app/globals.css` - responsive layout styles

## Definition of Done

- [x] All planned first-screen surfaces are visible.
- [x] Layout is responsive at narrow viewport.
- [x] Text does not overlap or overflow in visual audit.
- [x] `npm run build` passes.
- [x] Targeted tests green in local runtime; Docker daemon unavailable in this session.
- [x] Post-task audit done

## Improvements noted

- Screenshot capture through the in-app browser timed out intermittently; DOM
  audit passed all required surface checks.
