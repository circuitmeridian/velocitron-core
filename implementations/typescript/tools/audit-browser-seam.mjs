import {readdir, readFile} from "node:fs/promises";
import {fileURLToPath} from "node:url";
import {dirname, resolve} from "node:path";

const packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const roots = [
  resolve(packageRoot, "src"),
  resolve(packageRoot, "node_modules/@marcbachmann/cel-js/lib"),
];
const sourceExtensions = new Set([".js", ".ts"]);
const forbidden = [
  ["CommonJS require", /\brequire\s*\(/u],
  ["dynamic Function constructor", /\b(?:new\s+)?Function\s*\(/u],
  ["WebAssembly", /\bWebAssembly\b/u],
  ["Node protocol import", /["']node:/u],
  ["Node builtin import", /\bfrom\s+["'](?:fs|path|process|child_process|module)["']/u],
  ["global eval call", /(^|[^\w$.])eval\s*\(/mu],
];

async function sourceFiles(root) {
  const files = [];
  const pending = [root];
  while (pending.length > 0) {
    const directory = pending.pop();
    if (directory === undefined) continue;
    const entries = await readdir(directory, {withFileTypes: true});
    entries.sort((left, right) => left.name.localeCompare(right.name));
    for (const entry of entries) {
      const path = resolve(directory, entry.name);
      if (entry.isDirectory()) {
        pending.push(path);
      } else if (sourceExtensions.has(entry.name.slice(entry.name.lastIndexOf(".")))) {
        files.push(path);
      }
    }
  }
  return files.sort();
}

const files = (await Promise.all(roots.map(sourceFiles))).flat().sort();
const violations = [];
for (const path of files) {
  const source = await readFile(path, "utf8");
  for (const [label, pattern] of forbidden) {
    if (pattern.test(source)) violations.push(`${path}: ${label}`);
  }
}

if (violations.length > 0) {
  console.error(`Browser seam audit failed:\n${violations.join("\n")}`);
  process.exitCode = 1;
} else {
  console.log(`Browser seam audit passed (${files.length} source files).`);
}
