import {describe, expect, it} from "vitest";
import {compilePetrinetText, PetrinetDslError} from "../../src/dsl/api.js";
import {lowerPetrinetText} from "../../src/dsl/internal.js";
import {NetValidationError, parseNet} from "../../src/index.js";
import type {Net} from "../../src/schema/types.js";

const DECREMENT_SOURCE = '"{\\"n\\": binding.counter[0].n - 1}"';
const DECREMENT = '{"n": binding.counter[0].n - 1}';

function counterSource(facts: string): string {
  return [
    "net counter",
    "",
    "@sell_loop: (counter) -count-> [sell] -count-> (counter)",
    '[sell] handler "sell"',
    facts,
    "",
  ].join("\n");
}

function expectDslDiagnostic(
  action: () => unknown,
  code: string,
  message: string,
): void {
  try {
    action();
  } catch (error) {
    expect(error).toBeInstanceOf(PetrinetDslError);
    expect((error as PetrinetDslError).diagnostic).toMatchObject({code, message});
    return;
  }
  throw new Error("expected a Petri-net DSL diagnostic");
}

describe("data cel arc fact (ADR 0023)", () => {
  it("lowers to the produce template's cel field and parses as a net", () => {
    // given: a counter net whose produce end carries a computed fallback
    const source = counterSource(`@sell_loop data cel ${DECREMENT_SOURCE}`);

    // when: compiling the source to canonical JSON
    const compiled = compilePetrinetText(source, "counter.petrinet");

    // then: the produce arc carries cel and no literal data
    expect(compiled.documentKind).toBe("net");
    const arcs = compiled.document.arcs as readonly Record<string, unknown>[];
    const produce = arcs.find((arc) => arc.produce !== undefined)?.produce as Record<string, unknown>;
    expect(produce).toEqual({type: "count", destination: "counter", cel: DECREMENT});

    // and: the compiled JSON round-trips through core parsing (rule 14)
    const net: Net = parseNet(compiled.document);
    expect(net.arcs.find((arc) => arc.produce !== undefined)?.produce?.cel).toBe(DECREMENT);
  });

  it("treats equal repeated data cel facts as idempotent", () => {
    // given: the same data cel fact declared twice
    const source = counterSource([
      `@sell_loop data cel ${DECREMENT_SOURCE}`,
      `@sell_loop data cel ${DECREMENT_SOURCE}`,
    ].join("\n"));

    // when/then: compilation succeeds with one cel on the template
    const compiled = compilePetrinetText(source, "counter.petrinet");
    const arcs = compiled.document.arcs as readonly Record<string, unknown>[];
    const produce = arcs.find((arc) => arc.produce !== undefined)?.produce as Record<string, unknown>;
    expect(produce.cel).toBe(DECREMENT);
  });

  it("diagnoses differing data cel facts as a conflict", () => {
    // given: two differing cel expressions on one handle
    // when/then: the second declaration conflicts like duplicate data facts
    expectDslDiagnostic(
      () => compilePetrinetText(counterSource([
        `@sell_loop data cel ${DECREMENT_SOURCE}`,
        '@sell_loop data cel "{\\"n\\": 0}"',
      ].join("\n")), "counter.petrinet"),
      "PN202",
      "conflicting data cel facts for arc @sell_loop",
    );
  });

  it("lowers to the portable arc.produce-cel contribution kind", () => {
    // given: a data cel fact
    const source = counterSource(`@sell_loop data cel ${DECREMENT_SOURCE}`);

    // when: lowering to Contribution IR
    const ir = lowerPetrinetText(source, "counter.petrinet");

    // then: the contribution matches the cross-implementation encoding
    expect(ir.contributions.map(({kind, target, value}) => ({kind, target, value})))
      .toContainEqual({
        kind: "arc.produce-cel",
        target: {type: "arcHandle", name: "sell_loop"},
        value: {cel: DECREMENT},
      });
  });

  it("rejects mixing literal data and data cel on one arc", () => {
    // given: one literal and one computed fallback on the same handle
    // when/then: the XOR is diagnosed regardless of declaration order
    expectDslDiagnostic(
      () => compilePetrinetText(counterSource([
        '@sell_loop data {"n": 0}',
        `@sell_loop data cel ${DECREMENT_SOURCE}`,
      ].join("\n")), "counter.petrinet"),
      "PN202",
      "arc @sell_loop declares both data and data cel; they are mutually exclusive",
    );
    expectDslDiagnostic(
      () => compilePetrinetText(counterSource([
        `@sell_loop data cel ${DECREMENT_SOURCE}`,
        '@sell_loop data {"n": 0}',
      ].join("\n")), "counter.petrinet"),
      "PN202",
      "arc @sell_loop declares both data and data cel; they are mutually exclusive",
    );
  });

  it("rejects invalid CEL in a data cel fact at resolution", () => {
    // given: a syntactically invalid expression
    // when/then: resolution diagnoses the CEL like predicate/correlate facts
    expectDslDiagnostic(
      () => compilePetrinetText(
        counterSource('@sell_loop data cel "1 +"'),
        "counter.petrinet",
      ),
      "PN203",
      "invalid CEL data expression for arc @sell_loop",
    );
  });

  it("rejects an empty data cel expression as an invalid contribution", () => {
    expectDslDiagnostic(
      () => compilePetrinetText(
        counterSource('@sell_loop data cel ""'),
        "counter.petrinet",
      ),
      "PN200",
      "invalid arc produce-cel contribution",
    );
  });

  it("passes a bare-string literal data fact through to core validation", () => {
    // given: a literal data fact whose value is a bare JSON string
    // when: compiling (the DSL stage stops before core-schema validation)
    const compiled = compilePetrinetText(
      counterSource('@sell_loop data "not-an-object"'),
      "counter.petrinet",
    );

    // then: the literal lands verbatim and core parsing rejects it (data
    // must be an object), matching the Python implementation's split
    const arcs = compiled.document.arcs as readonly Record<string, unknown>[];
    expect(arcs.find((arc) => arc.produce !== undefined)?.produce)
      .toEqual({type: "count", destination: "counter", data: "not-an-object"});
    expect(() => parseNet(compiled.document)).toThrow(NetValidationError);
  });
});
