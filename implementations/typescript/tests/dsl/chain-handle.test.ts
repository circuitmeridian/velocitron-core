import {describe, expect, it} from "vitest";
import {compilePetrinetText, PetrinetDslError} from "../../src/dsl/api.js";

// A four-arc handled run: place-first, alternating, so the handle covers two
// consume arcs (a->b, c->d) and two produce arcs (b->c, d->e).
const HANDLED_RUN = "@run: (a) -> [b] -> (c) -> [d] -> (e)";
const UNHANDLED_RUN = "(a) -> [b] -> (c) -> [d] -> (e)";

function longChainSource(chain: string, facts: readonly string[] = []): string {
  return ["net long_chain", "", chain, ...facts, ""].join("\n");
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

describe("arbitrary-length chain handles", () => {
  it("compiles a handled four-arc run identically to the unhandled form", () => {
    // given: the same four-arc run, once with a chain handle and once without
    const handled = longChainSource(HANDLED_RUN);
    const unhandled = longChainSource(UNHANDLED_RUN);

    // when: compiling both sources to canonical JSON
    const compiledHandled = compilePetrinetText(handled, "long-chain.petrinet");
    const compiledUnhandled = compilePetrinetText(unhandled, "long-chain.petrinet");

    // then: the handled run resolves to a net with all four expanded arcs
    expect(compiledHandled.documentKind).toBe("net");
    const arcs = compiledHandled.document.arcs as readonly Record<string, unknown>[];
    expect(arcs.map((arc) => [arc.from, arc.to])).toEqual([
      [{place: "a"}, {transition: "b"}],
      [{transition: "b"}, {place: "c"}],
      [{place: "c"}, {transition: "d"}],
      [{transition: "d"}, {place: "e"}],
    ]);
    // and: the non-persisted handle leaves no trace — the documents are equal
    expect(compiledHandled.document).toEqual(compiledUnhandled.document);
  });

  it("keeps the exactly-one-input-arc weight diagnostic on a multi-arc handle", () => {
    // given: a weight fact on a handle covering two consume arcs
    // when/then: resolution diagnoses the ambiguity, not a generic handle error
    expectDslDiagnostic(
      () => compilePetrinetText(
        longChainSource(HANDLED_RUN, ["@run weight 2"]),
        "long-chain.petrinet",
      ),
      "PN202",
      "arc handle @run must identify exactly one input arc for weight",
    );
  });

  it("keeps the metadata exactly-one-arc diagnostic on a described multi-arc handle", () => {
    // given: a description fact on a handle covering four arcs
    // when/then: the persisted-handle rule diagnoses the multi-arc handle
    expectDslDiagnostic(
      () => compilePetrinetText(
        longChainSource(HANDLED_RUN, ['@run description "the whole run"']),
        "long-chain.petrinet",
      ),
      "PN202",
      "metadata arc handle '@run' must identify exactly one arc",
    );
  });
});
