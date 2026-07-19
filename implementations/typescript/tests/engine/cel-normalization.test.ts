import {describe, expect, it} from "vitest";
import {CelEvaluationError} from "../../src/cel/errors.js";
import {DefaultCelAdapter} from "../../src/cel/default.js";

describe("DefaultCelAdapter JSON numeric contract", () => {
  it("recursively promotes safe integral JSON numbers to CEL int and normalizes safe results", () => {
    // given: integral values nested through objects and arrays
    const adapter = new DefaultCelAdapter();
    const program = adapter.compile("outer.values[0] + outer.values[1].amount + 1");

    // when: CEL integer arithmetic evaluates the JSON context
    const result = adapter.evaluate(program, {
      outer: {values: [2, {amount: 3}]},
    });

    // then: arithmetic matches Python integers and the safe bigint result crosses as JSON number
    expect(result).toBe(6);
    expect(typeof result).toBe("number");
  });

  it("retains non-integral JSON numbers as CEL doubles", () => {
    // given: a fractional JSON value and a decimal CEL literal
    const adapter = new DefaultCelAdapter();
    const program = adapter.compile("n + 0.25");

    // when/then: the double overload remains available and finite
    expect(adapter.evaluate(program, {n: 1.5})).toBe(1.75);
  });

  it("normalizes safe integers recursively in result arrays", () => {
    // given: a CEL list containing direct and computed integers
    const adapter = new DefaultCelAdapter();
    const program = adapter.compile("[[n + 1], [n + 2], [n + 3]]");

    // when/then: no bigint escapes the adapter's JSON surface
    expect(adapter.evaluate(program, {n: 4})).toEqual([[5], [6], [7]]);
  });

  it("preserves reserved JSON object keys without invoking prototype setters", () => {
    // given: an ordinary JSON context with an own __proto__ data member
    const adapter = new DefaultCelAdapter();
    const program = adapter.compile("payload");
    const context = JSON.parse("{\"payload\":{\"__proto__\":3}}") as {
      readonly payload: Readonly<Record<string, number>>;
    };

    // when: CEL returns the object across the normalized JSON boundary
    const result = adapter.evaluate(program, context) as Readonly<Record<string, unknown>>;

    // then: the key remains own data and safe integer normalization still applies
    expect(Object.hasOwn(result, "__proto__")).toBe(true);
    expect(Object.getPrototypeOf(result)).toBeNull();
    expect(result.__proto__).toBe(3);
  });

  it("rejects unsafe integer and non-JSON results before they cross the adapter boundary", () => {
    // given: CEL values outside the JSON-safe result surface
    const adapter = new DefaultCelAdapter();
    const unsafe = adapter.compile("9007199254740992");
    const bytes = adapter.compile("b'abc'");

    // when/then: both failures are stable normalized evaluation errors
    // Bite: returning backend bigint/Uint8Array values would leak non-JSON data into records.
    expect(() => adapter.evaluate(unsafe, {})).toThrowError(expect.objectContaining({
      diagnostic: expect.objectContaining({phase: "eval", code: "invalid_result"}),
    }));
    expect(() => adapter.evaluate(bytes, {})).toThrow(CelEvaluationError);
  });
});
