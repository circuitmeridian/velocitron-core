import {describe, expect, it} from "vitest";
import {NetValidationError, parseNet} from "../../src/index.js";

const DECREMENT = '{"n": binding.counter[0].n - 1}';

function counterDocument(produceExtras: Record<string, unknown>): unknown {
  return {
    name: "counter-net",
    places: [{name: "counter", accepts: ["count"]}],
    transitions: [{name: "sell", handler: "sell"}],
    arcs: [
      {from: {place: "counter"}, to: {transition: "sell"}, consume: {type: "count"}},
      {
        from: {transition: "sell"},
        to: {place: "counter"},
        produce: {type: "count", destination: "counter", ...produceExtras},
      },
    ],
  };
}

function validationError(action: () => unknown): NetValidationError {
  try {
    action();
  } catch (error) {
    expect(error).toBeInstanceOf(NetValidationError);
    return error as NetValidationError;
  }
  throw new Error("expected NetValidationError");
}

describe("produce template cel (rule 14, ADR 0023)", () => {
  it("rejects a template declaring both data and cel at parse", () => {
    // given: a produce template carrying both fallbacks
    // when/then: parsing fails before any engine work (schema not-clause)
    const error = validationError(() =>
      parseNet(counterDocument({cel: DECREMENT, data: {n: 0}})),
    );
    expect(error.issues.some((issue) => issue.path.startsWith("/arcs/1/produce"))).toBe(true);
  });

  it("rejects invalid cel at parse, not fire", () => {
    // given: a produce cel with a syntax error
    // when/then: parseNet itself raises with the compile-failure issue
    const error = validationError(() =>
      parseNet(counterDocument({cel: '{"n": binding.counter[0].n -'})),
    );
    expect(error.issues).toEqual([
      expect.objectContaining({
        code: "arc.produce.cel_invalid",
        path: "/arcs/1/produce/cel",
      }),
    ]);
  });

  it("round-trips a valid cel onto the parsed template", () => {
    // given/when: a parsed counter net
    const net = parseNet(counterDocument({cel: DECREMENT}));

    // then: the produce arc's template carries the expression
    const produce = net.arcs.find((arc) => arc.produce !== undefined)?.produce;
    expect(produce?.cel).toBe(DECREMENT);
    // and: literal data stays absent (XOR held)
    expect(produce?.data).toBeUndefined();
  });
});
