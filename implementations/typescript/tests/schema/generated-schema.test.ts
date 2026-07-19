import {describe, expect, it} from "vitest";
import canonicalCompositionSchema from "../../../../spec/composition.schema.json";
import canonicalNetSchema from "../../../../spec/net.schema.json";
import {
  COMPOSITION_SCHEMA,
  NET_SCHEMA,
} from "../../src/schema/generated/schemas.js";
import {
  validateComposition,
  validateNet,
} from "../../src/schema/generated/validators.js";
import validatorSource from "../../src/schema/generated/validators.ts?raw";

describe("generated canonical schemas", () => {
  it("matches both root schema documents exactly", () => {
    expect(NET_SCHEMA).toEqual(canonicalNetSchema);
    expect(COMPOSITION_SCHEMA).toEqual(canonicalCompositionSchema);
  });

  it("contains standalone browser-safe validators", () => {
    expect(validatorSource).toContain("export const validateNet");
    expect(validatorSource).toContain("export const validateComposition");
    expect(validatorSource).not.toMatch(/\brequire\s*\(/u);
    expect(validatorSource).not.toMatch(/\b(?:new\s+)?Function\s*\(/u);
    expect(validatorSource).not.toMatch(/\bnode:/u);
    expect(validatorSource).not.toMatch(/from\s+["'](?:node:)?(?:fs|path|module)["']/u);
  });

  it("validates without compiling schemas at import time", () => {
    expect(validateNet({arcs: [], name: "empty", places: [], transitions: []})).toBe(true);
    expect(validateNet({arcs: [], name: "", places: [], transitions: []})).toBe(false);
    expect(validateComposition({nets: [{ref: "net.json"}], wires: []})).toBe(true);
    expect(validateComposition({nets: [], wires: []})).toBe(false);
  });
});
