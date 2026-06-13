import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const dashboardRoot = join(dirname(fileURLToPath(import.meta.url)), "..");
const repoRoot = join(dashboardRoot, "..");
const generatedPath = join(dashboardRoot, "lib", "generated", "openapi.ts");

function pythonExecutable() {
  if (process.env.PYTHON) {
    return process.env.PYTHON;
  }
  const venvPython = join(repoRoot, ".venv", "bin", "python");
  return existsSync(venvPython) ? venvPython : "python3";
}

function loadOpenApi() {
  const code = [
    "import json",
    "from lucy.api.app import create_app",
    "print(json.dumps(create_app().openapi(), sort_keys=True))"
  ].join("\n");
  const result = spawnSync(pythonExecutable(), ["-c", code], {
    cwd: repoRoot,
    env: {
      ...process.env,
      PYTHONPATH: join(repoRoot, "src")
    },
    encoding: "utf8"
  });

  if (result.status !== 0) {
    throw new Error(result.stderr || result.stdout || "OpenAPI generation failed");
  }
  return JSON.parse(result.stdout);
}

function refName(ref) {
  return ref.split("/").at(-1);
}

function literal(value) {
  return JSON.stringify(value);
}

function schemaToTs(schema) {
  if (!schema) {
    return "unknown";
  }
  if (schema.$ref) {
    return refName(schema.$ref);
  }
  if (schema.anyOf || schema.oneOf) {
    const variants = schema.anyOf || schema.oneOf;
    return variants.map(schemaToTs).join(" | ");
  }
  if (schema.allOf) {
    return schema.allOf.map(schemaToTs).join(" & ");
  }
  if (schema.enum) {
    return schema.enum.map(literal).join(" | ");
  }
  if (schema.const !== undefined) {
    return literal(schema.const);
  }

  if (schema.type === "array") {
    const itemType = schemaToTs(schema.items);
    return itemType.includes(" | ") ? `(${itemType})[]` : `${itemType}[]`;
  }
  if (schema.type === "integer" || schema.type === "number") {
    return "number";
  }
  if (schema.type === "boolean") {
    return "boolean";
  }
  if (schema.type === "string") {
    return "string";
  }
  if (schema.type === "object" || schema.properties) {
    const properties = schema.properties || {};
    const required = new Set(schema.required || []);
    const lines = Object.keys(properties)
      .sort()
      .map((name) => {
        const optional = required.has(name) ? "" : "?";
        return `  ${JSON.stringify(name)}${optional}: ${schemaToTs(properties[name])};`;
      });
    if (schema.additionalProperties && schema.additionalProperties !== true) {
      lines.push(`  [key: string]: ${schemaToTs(schema.additionalProperties)};`);
    } else if (schema.additionalProperties === true) {
      lines.push("  [key: string]: unknown;");
    }
    return lines.length > 0 ? `{\n${lines.join("\n")}\n}` : "Record<string, never>";
  }
  return "unknown";
}

function emitTypes(spec) {
  const schemas = spec.components?.schemas || {};
  const schemaNames = Object.keys(schemas).sort();
  const paths = Object.keys(spec.paths || {}).sort();
  const lines = [
    "/* eslint-disable */",
    "// Generated from Lucy FastAPI OpenAPI. Run `npm run generate:openapi`.",
    "",
    `export const openApiVersion = ${literal(spec.openapi)};`,
    `export const openApiTitle = ${literal(spec.info?.title || "Lucy API")};`,
    "export const openApiPaths = [",
    ...paths.map((path) => `  ${literal(path)},`),
    "] as const;",
    "export type OpenApiPath = (typeof openApiPaths)[number];",
    ""
  ];

  for (const name of schemaNames) {
    lines.push(`export type ${name} = ${schemaToTs(schemas[name])};`, "");
  }

  lines.push("");
  return lines.join("\n");
}

const spec = loadOpenApi();
mkdirSync(dirname(generatedPath), { recursive: true });
writeFileSync(generatedPath, emitTypes(spec));
console.log(`generated ${generatedPath}`);
