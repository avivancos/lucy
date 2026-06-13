import { readFileSync } from "node:fs";
import { join } from "node:path";

const root = new URL("..", import.meta.url).pathname;

function read(path) {
  return readFileSync(join(root, path), "utf8");
}

function assertIncludes(path, expected) {
  const text = read(path);
  if (!text.includes(expected)) {
    throw new Error(`${path} must include ${expected}`);
  }
}

function assertFile(path) {
  read(path);
}

for (const path of [
  "package.json",
  "tsconfig.json",
  "next.config.mjs",
  "Dockerfile",
  "app/layout.tsx",
  "app/globals.css",
]) {
  assertFile(path);
}

assertIncludes("package.json", "\"next\"");
assertIncludes("package.json", "\"typescript\"");
assertIncludes("package.json", "\"test:contracts\"");
assertIncludes("package.json", "\"generate:openapi\"");
assertIncludes("app/layout.tsx", "Lucy Ops Command Center");
assertIncludes("app/globals.css", ".metricsGrid");
assertIncludes("app/globals.css", "@media");

assertFile("lib/generated/openapi.ts");
assertIncludes("lib/generated/openapi.ts", "Generated from Lucy FastAPI OpenAPI");
assertIncludes("lib/generated/openapi.ts", "export type SessionSummary");
assertIncludes("lib/generated/openapi.ts", "export type TraceSummary");

assertIncludes("lib/api.ts", "from \"./generated/openapi\"");
assertIncludes("lib/api.ts", "SessionSummary");
assertIncludes("lib/api.ts", "cost_per_minute");
assertIncludes("lib/api.ts", "demoSessions");

for (const expected of [
  "Cost per minute",
  "Latency p95",
  "Sentiment",
  "Funnel stage",
  "Live Call",
  "RAG Inspector",
  "Voice Naturalizer",
  "MCP Tools",
]) {
  assertIncludes("app/page.tsx", expected);
}

console.log("dashboard contract ok");
