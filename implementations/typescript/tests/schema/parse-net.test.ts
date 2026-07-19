import {describe, expect, it} from "vitest";
import cadenceTick from "../../../../spec/conformance/petrinet/09-cadence-tick/cadence-tick.net.json";
import coinDeposit from "../../../../spec/conformance/petrinet/01-coin-deposit/coin-deposit.net.json";
import perKeySuppression from "../../../../spec/conformance/petrinet/07-per-key-suppression/per-key-suppression.net.json";
import wiredPulse from "../../../../spec/conformance/petrinet/10-wired-pulse/wired_pulse.composition.json";
import {
  NetValidationError,
  parseCompositionShape,
  parseNet,
  type CelAdapter,
} from "../../src/index.js";
import {JSON_SNAPSHOT_LIMITS} from "../../src/schema/json.js";

function copy<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
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

describe("parseNet", () => {
  it("parses shared canonical fixtures and preserves declaration order", () => {
    const coin = parseNet(coinDeposit);
    const cadence = parseNet(cadenceTick);
    const suppression = parseNet(perKeySuppression);

    expect(coin.name).toBe("coin_deposit");
    expect(coin.places.map((place) => place.name)).toEqual(
      coinDeposit.places.map((place) => place.name),
    );
    expect(cadence.transitions.map((transition) => transition.name)).toEqual(
      cadenceTick.transitions.map((transition) => transition.name),
    );
    expect(suppression.arcs).toHaveLength(perKeySuppression.arcs.length);
  });

  it("normalizes defaults, remains deterministic, and deeply freezes output", () => {
    const document = {
      arcs: [
        {
          consume: {type: "token"},
          from: {place: "ready"},
          to: {transition: "advance"},
        },
      ],
      initialMarking: {ready: [{data: {}, type: "token"}]},
      name: "handlerless",
      places: [{accepts: ["token"], name: "ready"}],
      transitions: [{name: "advance"}],
    };

    const first = parseNet(document);
    const second = parseNet(copy(document));
    expect(first).toEqual(second);
    expect(first.arcs[0]?.consume).toMatchObject({mode: "consume", weight: 1});
    expect(first.transitions[0]?.handler).toBeUndefined();
    expect(Object.isFrozen(first)).toBe(true);
    expect(Object.isFrozen(first.places)).toBe(true);
    expect(Object.isFrozen(first.initialMarking?.ready)).toBe(true);
    expect(() => {
      (first.places as Array<unknown>).push({});
    }).toThrow(TypeError);
  });

  it("reports every Ajv schema issue through stable copied fields", () => {
    const error = validationError(() =>
      parseNet({
        arcs: "not-an-array",
        initialMarking: {p: [{type: "token"}]},
        name: "",
        places: [{accepts: [], extra: true, name: ""}],
        transitions: [{handler: null, name: ""}],
      }),
    );

    expect(error.issues.length).toBeGreaterThanOrEqual(7);
    expect(new Set(error.issues.map((issue) => issue.code))).toEqual(new Set(["schema.invalid"]));
    expect(error.issues.some((issue) => issue.path === "/initialMarking/p/0")).toBe(true);
    for (const issue of error.issues) {
      expect(issue.path).toMatch(/^\//u);
      expect(issue.details?.keyword).toBeTypeOf("string");
      expect(issue.details?.schemaPath).toBeTypeOf("string");
      expect(Object.isFrozen(issue)).toBe(true);
    }
  });

  it("collects parser-authoritative structural failures in declaration order", () => {
    const error = validationError(() =>
      parseNet({
        arcs: [
          {
            consume: {correlate: {cel: "true"}, mode: "read", type: "z"},
            from: {place: "p"},
            to: {transition: "t"},
          },
          {
            consume: {mode: "inhibit", type: "a", weight: 2},
            from: {place: "p"},
            to: {transition: "t"},
          },
          {
            from: {transition: "t"},
            produce: {destination: "other", type: "z"},
            to: {place: "p"},
          },
          {
            consume: {type: "a"},
            from: {place: "missing_place"},
            to: {transition: "missing_transition"},
          },
        ],
        name: "invalid-structure",
        places: [
          {accepts: ["a"], name: "p", port: {direction: "input", type: "c"}},
          {accepts: ["b"], name: "p"},
        ],
        transitions: [
          {
            name: "t",
            timer: {
              bind: {bad: "other", clock: "p"},
              cel: "true",
              clock: "missing_clock",
            },
          },
          {name: "t"},
        ],
      }),
    );

    expect(error.issues.map((issue) => issue.code)).toEqual([
      "port.type_not_accepted",
      "place.duplicate_name",
      "transition.duplicate_name",
      "arc.consume.type_not_accepted",
      "arc.correlate.mode_invalid",
      "arc.inhibit.weight_not_allowed",
      "arc.produce.destination_mismatch",
      "arc.produce.type_not_accepted",
      "arc.place_undeclared",
      "arc.transition_undeclared",
      "timer.clock_undeclared",
      "timer.bind.source_invalid",
      "timer.bind.clock_reserved",
    ]);
    expect(error.issues.map((issue) => issue.path)).toEqual([
      "/places/0/port/type",
      "/places/1/name",
      "/transitions/1/name",
      "/arcs/0/consume/type",
      "/arcs/0/consume/correlate",
      "/arcs/1/consume/weight",
      "/arcs/2/produce/destination",
      "/arcs/2/produce/type",
      "/arcs/3/from/place",
      "/arcs/3/to/transition",
      "/transitions/0/timer/clock",
      "/transitions/0/timer/bind/bad",
      "/transitions/0/timer/bind/clock",
    ]);
  });

  it("compiles every inline CEL surface at parse time through an injected adapter", () => {
    const expressions: string[] = [];
    const adapter: CelAdapter = {
      compile(source) {
        expressions.push(source);
        return Object.freeze({source});
      },
      evaluate() {
        return true;
      },
    };
    parseNet(
      {
        arcs: [
          {
            consume: {predicate: {cel: "ok == true"}, type: "token"},
            from: {place: "p"},
            to: {transition: "t"},
          },
          {
            consume: {
              correlate: {cel: "token.id == binding.p[0].id"},
              mode: "inhibit",
              type: "token",
            },
            from: {place: "blocked"},
            to: {transition: "t"},
          },
        ],
        name: "all-cel-surfaces",
        places: [
          {accepts: ["token"], name: "p"},
          {accepts: ["token"], name: "blocked"},
          {accepts: ["clock"], name: "clock"},
        ],
        transitions: [
          {
            name: "t",
            timer: {
              bind: {item: "p"},
              cel: "clock.now >= item.deadline",
              clock: "clock",
              maturity: "item.deadline",
            },
          },
        ],
      },
      {celAdapter: adapter},
    );

    expect(expressions).toEqual([
      "ok == true",
      "token.id == binding.p[0].id",
      "clock.now >= item.deadline",
      "item.deadline",
    ]);
  });

  it("rejects malformed CEL during parse and exposes no compiled backend values", () => {
    const document = {
      arcs: [
        {
          consume: {predicate: {cel: "token.("}, type: "token"},
          from: {place: "p"},
          to: {transition: "t"},
        },
      ],
      name: "bad-cel",
      places: [{accepts: ["token"], name: "p"}],
      transitions: [{name: "t"}],
    };
    const error = validationError(() => parseNet(document));

    expect(error.issues).toHaveLength(1);
    expect(error.issues[0]).toMatchObject({
      code: "arc.predicate.cel_invalid",
      path: "/arcs/0/consume/predicate/cel",
    });
    expect(error.issues[0]?.details?.cel).toMatchObject({phase: "compile"});
    expect(JSON.stringify(error)).not.toContain("ParseResult");
  });

  it("rejects non-JSON and non-finite values before schema validation without invoking getters", () => {
    let invoked = false;
    const getterDocument = copy(coinDeposit) as Record<string, unknown>;
    Object.defineProperty(getterDocument, "unsafe", {
      enumerable: true,
      get() {
        invoked = true;
        return true;
      },
    });
    const getterError = validationError(() => parseNet(getterDocument));
    expect(invoked).toBe(false);
    expect(getterError.issues[0]?.code).toBe("json.invalid_property");

    const nonFinite = copy(coinDeposit) as Record<string, unknown>;
    nonFinite.annotations = {value: Number.NaN};
    const numberError = validationError(() => parseNet(nonFinite));
    expect(numberError.issues[0]).toMatchObject({
      code: "json.non_finite_number",
      path: "/annotations/value",
    });
  });

  it("accepts depth 64 and rejects depth 65 before schema validation", () => {
    const documentWithLeafAtDepth = (leafDepth: number): Record<string, unknown> => {
      const annotations: Record<string, unknown> = {};
      let cursor = annotations;
      for (let containerDepth = 1; containerDepth < leafDepth - 1; containerDepth++) {
        const next: Record<string, unknown> = {};
        cursor.next = next;
        cursor = next;
      }
      cursor.value = "leaf";
      return {
        annotations,
        arcs: [],
        name: `depth-${leafDepth}`,
        places: [],
        transitions: [],
      };
    };

    expect(() =>
      parseNet(documentWithLeafAtDepth(JSON_SNAPSHOT_LIMITS.nestingDepth)),
    ).not.toThrow();

    const error = validationError(() =>
      parseNet(documentWithLeafAtDepth(JSON_SNAPSHOT_LIMITS.nestingDepth + 1)),
    );
    expect(error.issues).toHaveLength(1);
    expect(error.issues[0]).toMatchObject({
      code: "json.max_depth",
      message: "JSON nesting depth exceeds the limit of 64",
    });
  });

  it("rejects oversized and huge sparse arrays with one bounded resource issue", () => {
    for (const length of [
      JSON_SNAPSHOT_LIMITS.containerEntries + 1,
      2 ** 32 - 1,
    ]) {
      const error = validationError(() => parseNet(new Array<unknown>(length)));
      expect(error.issues).toEqual([
        {
          code: "json.max_container_entries",
          message: "JSON container entry count exceeds the limit of 50000",
          path: "",
        },
      ]);
    }
  });

  it("halts snapshot traversal when the total JSON value budget is exhausted", () => {
    const document = {
      annotations: {
        first: new Array<null>(JSON_SNAPSHOT_LIMITS.containerEntries).fill(null),
        second: new Array<null>(JSON_SNAPSHOT_LIMITS.containerEntries).fill(null),
      },
      arcs: [],
      name: "total-value-limit",
      places: [],
      transitions: [],
    };

    const error = validationError(() => parseNet(document));
    expect(error.issues).toEqual([
      {
        code: "json.max_total_values",
        message: "JSON total value count exceeds the limit of 100000",
        path: "/annotations/second",
      },
    ]);
  });

  it("rejects unsafe numbers while preserving safe boundaries and decimals", () => {
    const unsafe = {
      ...copy(coinDeposit),
      annotations: {
        negative: Number.MIN_SAFE_INTEGER - 1,
        positive: Number.MAX_SAFE_INTEGER + 1,
      },
    };
    const error = validationError(() => parseNet(unsafe));
    expect(error.issues).toEqual([
      {
        code: "json.unsafe_number",
        message: "JSON numbers must be between -9007199254740991 and 9007199254740991",
        path: "/annotations/negative",
      },
      {
        code: "json.unsafe_number",
        message: "JSON numbers must be between -9007199254740991 and 9007199254740991",
        path: "/annotations/positive",
      },
    ]);

    const safe = parseNet({
      ...copy(coinDeposit),
      annotations: {
        decimal: 1.25,
        maximum: Number.MAX_SAFE_INTEGER,
        minimum: Number.MIN_SAFE_INTEGER,
      },
    });
    expect(safe.annotations).toEqual({
      decimal: 1.25,
      maximum: Number.MAX_SAFE_INTEGER,
      minimum: Number.MIN_SAFE_INTEGER,
    });
  });

  it("copies prototype-sensitive JSON keys without polluting prototypes", () => {
    const document = JSON.parse(
      '{"name":"safe","places":[],"transitions":[],"arcs":[],"annotations":{"__proto__":{"polluted":true}}}',
    ) as unknown;
    const net = parseNet(document);
    expect(Object.hasOwn(net.annotations ?? {}, "__proto__")).toBe(true);
    expect(({} as {polluted?: boolean}).polluted).toBeUndefined();
  });
});

describe("parseCompositionShape", () => {
  it("validates and freezes a shared composition without loading refs", () => {
    const composition = parseCompositionShape(wiredPulse);
    expect(composition.nets.map((entry) => entry.ref)).toEqual(
      wiredPulse.nets.map((entry) => entry.ref),
    );
    expect(Object.isFrozen(composition)).toBe(true);
    expect(Object.isFrozen(composition.wires)).toBe(true);
  });

  it("returns structured shape errors for closed composition documents", () => {
    const error = validationError(() =>
      parseCompositionShape({nets: [{ref: "net.json", unexpected: true}], wires: []}),
    );
    expect(error.issues).toContainEqual(
      expect.objectContaining({code: "schema.invalid", path: "/nets/0"}),
    );
  });
});

