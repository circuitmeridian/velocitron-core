#!/usr/bin/env node

import { readdir, readFile } from "node:fs/promises";
import { builtinModules } from "node:module";
import { dirname, extname, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const coreSource = resolve(root, "implementations/typescript/src");
const sourceExtensions = new Set([".cjs", ".js", ".jsx", ".mjs", ".ts", ".tsx"]);
const nodeBuiltins = new Set(
  builtinModules.flatMap((specifier) => [specifier, `node:${specifier}`]),
);
const importPattern =
  /\b(?:import|export)\s+(?:type\s+)?(?:[^"'`;]*?\s+from\s*)?["']([^"']+)["']|\b(?:import|require)\s*\(\s*["']([^"']+)["']/g;

async function sourceFiles(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const files = [];
  for (const entry of entries.sort((left, right) => left.name.localeCompare(right.name))) {
    const path = resolve(directory, entry.name);
    if (entry.isDirectory()) {
      files.push(...(await sourceFiles(path)));
    } else if (entry.isFile() && sourceExtensions.has(extname(entry.name))) {
      files.push(path);
    }
  }
  return files;
}

const files = await sourceFiles(coreSource);
const violations = [];
for (const file of files) {
  const source = await readFile(file, "utf8");
  for (const match of source.matchAll(importPattern)) {
    const specifier = match[1] ?? match[2];
    if (specifier !== undefined && nodeBuiltins.has(specifier)) {
      const line = source.slice(0, match.index).split("\n").length;
      violations.push(`${relative(root, file)}:${line}: ${specifier}`);
    }
  }
}

if (violations.length > 0) {
  console.error("Node built-ins cross the @velocitron/core browser seam:");
  for (const violation of violations) {
    console.error(`  ${violation}`);
  }
  process.exitCode = 1;
} else {
  console.log(`Browser seam is clean (${files.length} source files checked)`);
}
