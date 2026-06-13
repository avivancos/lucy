# 5_1 - Create Next.js project shell

**Epic:** Dashboard
**Estimated effort:** ~1 h
**State:** done

## Goal

Create the dashboard app foundation with Next.js App Router and TypeScript.

## Spec

The `dashboard/` folder includes package metadata, TypeScript config, Next
config, Dockerfile, root layout, and global CSS.

## Files to create/modify

- `dashboard/package.json` - scripts and dependencies
- `dashboard/tsconfig.json` - TypeScript config
- `dashboard/next.config.mjs` - Next config
- `dashboard/app/layout.tsx` - root layout
- `dashboard/app/globals.css` - global styles

## Definition of Done

- [x] `npm run build` runs successfully.
- [x] Dashboard metadata names Lucy Ops Command Center.
- [x] Styles avoid a marketing landing-page feel.
- [x] Targeted tests green in local runtime; Docker daemon unavailable in this session.
- [x] Post-task audit done

## Improvements noted

- Full metric-surface visual audit belongs to `5_3`; shell audit confirmed title
  and local build, but screenshot capture was unstable in the in-app browser.
- Docker Compose verification should be rerun once the Docker daemon is active.
