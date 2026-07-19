import { compilePetrinetText, parseNet } from "../dist/index.js";

const compiled = compilePetrinetText(
  "net package_smoke\n\n(input) -> [advance]\n",
  "package-smoke.petrinet",
);

if (compiled.documentKind !== "net") {
  throw new Error(`expected a net document, got ${compiled.documentKind}`);
}

const net = parseNet(compiled.document);
if (net.name !== "package_smoke") {
  throw new Error(`expected package_smoke, got ${net.name}`);
}

console.log("Package import smoke passed.");
