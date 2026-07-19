import {describe, expect, it} from "vitest";

import {
  Engine,
  HandlerRegistry,
  mergeComposition,
  mergeNets,
  parseNet,
  type CompositionDocument,
  type CompositionWire,
  type Net,
  type Place,
  type Token,
} from "../../src/index.js";

// ── Fixtures ─────────────────────────────────────────────────────────────
// Each constituent net is parsed from its canonical JSON document, mirroring
// how the browser resolver hands merge_nets already-parsed nets.

function net(document: unknown): Net {
  return parseNet(document);
}

function producer(name = "producer"): Net {
  return net({
    name,
    places: [
      {name: "work", accepts: ["task"]},
      {name: "out", accepts: ["task"], port: {direction: "output", type: "task"}},
    ],
    transitions: [{name: "produce", handler: "produce_handler"}],
    arcs: [
      {from: {place: "work"}, to: {transition: "produce"}, consume: {type: "task"}},
      {
        from: {transition: "produce"},
        to: {place: "out"},
        produce: {type: "task", destination: "out"},
      },
    ],
  });
}

function consumer(name = "consumer"): Net {
  return net({
    name,
    places: [
      {name: "in", accepts: ["task"], port: {direction: "input", type: "task"}},
      {name: "done", accepts: ["task"]},
    ],
    transitions: [{name: "consume", handler: "consume_handler"}],
    arcs: [
      {from: {place: "in"}, to: {transition: "consume"}, consume: {type: "task"}},
      {
        from: {transition: "consume"},
        to: {place: "done"},
        produce: {type: "task", destination: "done"},
      },
    ],
  });
}

const WIRE: CompositionWire = {
  from: {net: "prod", port: "out"},
  to: {net: "cons", port: "in"},
};

function placeNames(result: Net): Set<string> {
  return new Set(result.places.map((place) => place.name));
}

function place(result: Net, name: string): Place {
  const found = result.places.find((candidate) => candidate.name === name);
  if (found === undefined) throw new Error(`no place named ${name}`);
  return found;
}

// ── M1: single net with no wires → disjoint union ────────────────────────

describe("mergeNets disjoint union (M1)", () => {
  it("qualifies every name and retains ports for a single net with no wires", () => {
    // given: a producer net and no wires
    const aliasToNet = new Map([["prod", producer()]]);

    // when: merging
    const result = mergeNets(aliasToNet, []);

    // then: places and transitions are alias-qualified
    const names = placeNames(result);
    expect(names.has("prod.work")).toBe(true);
    expect(names.has("prod.out")).toBe(true);
    expect(result.transitions.map((t) => t.name)).toContain("prod.produce");
    // and: arcs are rewritten to qualified endpoints
    const consume = result.arcs.find(
      (arc) => "transition" in arc.to && arc.to.transition === "prod.produce",
    );
    expect(consume && "place" in consume.from && consume.from.place).toBe("prod.work");
    // and: the output port is retained as a boundary port
    const out = place(result, "prod.out");
    expect(out.port).toEqual({direction: "output", type: "task"});
  });
});

// ── M2: one wire → fused place ───────────────────────────────────────────

describe("mergeNets single wire (M2)", () => {
  it("fuses the two ports and rewrites arcs to the fused place", () => {
    // given: a producer and consumer joined by one wire
    const aliasToNet = new Map([
      ["prod", producer()],
      ["cons", consumer()],
    ]);

    // when: merging
    const result = mergeNets(aliasToNet, [WIRE]);

    // then: the input port place is gone, the fused place is named after the source
    const names = placeNames(result);
    expect(names.has("cons.in")).toBe(false);
    expect(names.has("prod.out")).toBe(true);
    // and: the fused place has no port facet (no longer a boundary)
    expect(place(result, "prod.out").port).toBeUndefined();
    // and: the producer's produce arc deposits into the fused place
    const produce = result.arcs.find(
      (arc) => "transition" in arc.from && arc.from.transition === "prod.produce",
    );
    expect(produce && "place" in produce.to && produce.to.place).toBe("prod.out");
    // and: the consumer's consume arc reads from the fused place
    const consume = result.arcs.find(
      (arc) => "transition" in arc.to && arc.to.transition === "cons.consume",
    );
    expect(consume && "place" in consume.from && consume.from.place).toBe("prod.out");
    // and: non-port places stay qualified
    expect(names.has("prod.work")).toBe(true);
    expect(names.has("cons.done")).toBe(true);
  });

  it("retains unwired ports as boundary ports", () => {
    // given: an extra net with an unwired output port
    const extra = net({
      name: "extra",
      places: [
        {name: "internal", accepts: ["task"]},
        {name: "spare", accepts: ["task"], port: {direction: "output", type: "task"}},
      ],
      transitions: [{name: "noop", handler: "noop"}],
      arcs: [
        {from: {place: "internal"}, to: {transition: "noop"}, consume: {type: "task"}},
        {
          from: {transition: "noop"},
          to: {place: "spare"},
          produce: {type: "task", destination: "spare"},
        },
      ],
    });
    const aliasToNet = new Map([
      ["prod", producer()],
      ["cons", consumer()],
      ["extra", extra],
    ]);

    // when: merging with only the prod→cons wire
    const result = mergeNets(aliasToNet, [WIRE]);

    // then: the unwired port is still a boundary
    expect(place(result, "extra.spare").port).toEqual({
      direction: "output",
      type: "task",
    });
    // and: the wired ports fused into a non-boundary place
    expect(place(result, "prod.out").port).toBeUndefined();
  });
});

// ── M3: fan-out ──────────────────────────────────────────────────────────

describe("mergeNets fan-out (M3)", () => {
  it("collapses one output wired to two inputs into a single fused place", () => {
    // given: one producer and two consumers
    const aliasToNet = new Map([
      ["prod", producer()],
      ["cons1", consumer()],
      ["cons2", consumer("consumer2")],
    ]);
    const wires: CompositionWire[] = [
      {from: {net: "prod", port: "out"}, to: {net: "cons1", port: "in"}},
      {from: {net: "prod", port: "out"}, to: {net: "cons2", port: "in"}},
    ];

    // when: merging
    const result = mergeNets(aliasToNet, wires);

    // then: one fused place, both input ports gone
    const names = placeNames(result);
    expect(names.has("prod.out")).toBe(true);
    expect(names.has("cons1.in")).toBe(false);
    expect(names.has("cons2.in")).toBe(false);
    // and: both consumers read from the fused place
    for (const consumerAlias of ["cons1", "cons2"]) {
      const consume = result.arcs.find(
        (arc) =>
          "transition" in arc.to && arc.to.transition === `${consumerAlias}.consume`,
      );
      expect(consume && "place" in consume.from && consume.from.place).toBe("prod.out");
    }
  });
});

// ── M4: fan-in ───────────────────────────────────────────────────────────

describe("mergeNets fan-in (M4)", () => {
  it("collapses two outputs wired to one input, naming after sorted sources", () => {
    // given: two producers and one consumer
    const aliasToNet = new Map([
      ["prod1", producer()],
      ["prod2", producer("producer2")],
      ["cons", consumer()],
    ]);
    const wires: CompositionWire[] = [
      {from: {net: "prod1", port: "out"}, to: {net: "cons", port: "in"}},
      {from: {net: "prod2", port: "out"}, to: {net: "cons", port: "in"}},
    ];

    // when: merging
    const result = mergeNets(aliasToNet, wires);

    // then: one fused place named after both sorted source ports
    const names = placeNames(result);
    expect(names.has("prod1.out__prod2.out")).toBe(true);
    expect(names.has("prod1.out")).toBe(false);
    expect(names.has("prod2.out")).toBe(false);
    expect(names.has("cons.in")).toBe(false);
    // and: both producers deposit into the fused place
    for (const producerAlias of ["prod1", "prod2"]) {
      const produce = result.arcs.find(
        (arc) =>
          "transition" in arc.from && arc.from.transition === `${producerAlias}.produce`,
      );
      expect(produce && "place" in produce.to && produce.to.place).toBe(
        "prod1.out__prod2.out",
      );
    }
    // and: the consumer reads from the fused place
    const consume = result.arcs.find(
      (arc) => "transition" in arc.to && arc.to.transition === "cons.consume",
    );
    expect(consume && "place" in consume.from && consume.from.place).toBe(
      "prod1.out__prod2.out",
    );
  });
});

// ── Fused-place annotations ──────────────────────────────────────────────

function portOnlyNet(
  name: string,
  portName: string,
  direction: "input" | "output",
  annotations?: Record<string, unknown>,
): Net {
  return net({
    name,
    places: [
      {
        name: portName,
        accepts: ["task"],
        port: {direction, type: "task"},
        ...(annotations === undefined ? {} : {annotations}),
      },
    ],
    transitions: [],
    arcs: [],
  });
}

describe("mergeNets fused-place annotations", () => {
  it("tags every fused place with fusion:true and leaves non-fused places bare", () => {
    // given: a producer and consumer with un-annotated ports
    const aliasToNet = new Map([
      ["prod", producer()],
      ["cons", consumer()],
    ]);

    // when: merging
    const result = mergeNets(aliasToNet, [WIRE]);

    // then: the fused place carries the fusion tag
    expect(place(result, "prod.out").annotations).toEqual({fusion: true});
    // and: a non-fused place gains no annotations
    expect(place(result, "prod.work").annotations).toBeUndefined();
  });

  it("carries member annotations through with output-port precedence", () => {
    // given: an annotated output port and input port with a conflicting "team"
    const aliasToNet = new Map([
      ["prod", portOnlyNet("producer", "out", "output", {team: "prod-team", tier: 1})],
      ["cons", portOnlyNet("consumer", "in", "input", {team: "cons-team", sla: "1h"})],
    ]);

    // when: merging
    const result = mergeNets(aliasToNet, [WIRE]);

    // then: the output (source) port wins the conflicting key even though
    // "cons.in" sorts before "prod.out", and the fusion tag is set
    expect(place(result, "prod.out").annotations).toEqual({
      team: "prod-team",
      tier: 1,
      sla: "1h",
      fusion: true,
    });
  });

  it("overrides a member fusion annotation with the merge's own tag", () => {
    // given: an output port perversely annotated fusion=false
    const aliasToNet = new Map([
      ["prod", portOnlyNet("producer", "out", "output", {fusion: false})],
      ["cons", portOnlyNet("consumer", "in", "input")],
    ]);

    // when: merging
    const result = mergeNets(aliasToNet, [WIRE]);

    // then: the merge's fusion tag wins
    expect(place(result, "prod.out").annotations?.fusion).toBe(true);
  });
});

// ── M5: produce destination rewritten ────────────────────────────────────

describe("mergeNets produce destination (M5)", () => {
  it("rewrites the produce destination to the fused name", () => {
    // given: a producer and consumer with one wire
    const aliasToNet = new Map([
      ["prod", producer()],
      ["cons", consumer()],
    ]);

    // when: merging
    const result = mergeNets(aliasToNet, [WIRE]);

    // then: the producer's produce destination is the fused name
    const destinationFrom = (transition: string): string | undefined => {
      const arc = result.arcs.find(
        (candidate) =>
          "transition" in candidate.from && candidate.from.transition === transition,
      );
      return arc !== undefined && "produce" in arc && arc.produce !== undefined
        ? arc.produce.destination
        : undefined;
    };
    expect(destinationFrom("prod.produce")).toBe("prod.out");
    // and: a non-port destination stays qualified
    expect(destinationFrom("cons.consume")).toBe("cons.done");
  });
});

// ── M6: handler/guard refs not qualified ─────────────────────────────────

describe("mergeNets handler and guard refs (M6)", () => {
  it("qualifies transition names but leaves handler and guard refs untouched", () => {
    // given: a net with a handler and guard
    const guarded = net({
      name: "guarded",
      places: [
        {name: "src", accepts: ["task"]},
        {name: "dst", accepts: ["task"]},
      ],
      transitions: [{name: "t", handler: "my_handler", guard: "my_guard"}],
      arcs: [
        {from: {place: "src"}, to: {transition: "t"}, consume: {type: "task"}},
        {
          from: {transition: "t"},
          to: {place: "dst"},
          produce: {type: "task", destination: "dst"},
        },
      ],
    });

    // when: merging
    const result = mergeNets(new Map([["g", guarded]]), []);

    // then: the transition name is qualified but handler/guard are unchanged
    const transition = result.transitions.find((t) => t.name === "g.t");
    expect(transition?.handler).toBe("my_handler");
    expect(transition?.guard).toBe("my_guard");
  });
});

// ── M7: composed net is structurally valid ───────────────────────────────

describe("mergeNets validation (M7)", () => {
  it("returns a structurally valid net (parseNet does not raise)", () => {
    // given: a producer and consumer with one wire
    const aliasToNet = new Map([
      ["prod", producer()],
      ["cons", consumer()],
    ]);

    // when/then: merging validates the composed net without raising
    expect(() => mergeNets(aliasToNet, [WIRE])).not.toThrow();
  });
});

// ── M8: composed net runs end-to-end via Engine ──────────────────────────

describe("mergeNets runnable (M8)", () => {
  it("runs a token through the fused place from producer to consumer", () => {
    // given: a merged producer→consumer net
    const merged = mergeNets(
      new Map([
        ["prod", producer()],
        ["cons", consumer()],
      ]),
      [WIRE],
    );
    // and: handlers passing a task token through the fused place
    const registry = new HandlerRegistry();
    registry.registerTransition("produce_handler", () => ({
      status: "completed",
      outputTokens: {"prod.out": [{type: "task", data: {src: "producer"}}]},
      error: null,
      metadata: {},
    }));
    registry.registerTransition("consume_handler", () => ({
      status: "completed",
      outputTokens: {"cons.done": [{type: "task", data: {src: "consumer"}}]},
      error: null,
      metadata: {},
    }));
    const engine = new Engine(registry);
    const initial = {"prod.work": [{type: "task", data: {}} as Token]};

    // when: running
    const final = engine.run(merged, initial, {maxSteps: 10});

    // then: the token flowed through the fused place into cons.done
    expect(final["cons.done"]).toHaveLength(1);
    expect(final["cons.done"]?.[0]?.data.src).toBe("consumer");
    // and: the fused place is empty (the token was consumed)
    expect(final["prod.out"] ?? []).toHaveLength(0);
  });
});

// ── M9: initial markings compose ─────────────────────────────────────────

describe("mergeNets initial markings (M9)", () => {
  it("qualifies marking keys and leaves an unmarked fused place empty", () => {
    // given: a producer marked on "work" and a consumer marked on "done"
    const prod = net({
      name: "producer",
      places: [
        {name: "work", accepts: ["task"]},
        {name: "out", accepts: ["task"], port: {direction: "output", type: "task"}},
      ],
      transitions: [{name: "produce", handler: "produce_handler"}],
      arcs: [
        {from: {place: "work"}, to: {transition: "produce"}, consume: {type: "task"}},
        {
          from: {transition: "produce"},
          to: {place: "out"},
          produce: {type: "task", destination: "out"},
        },
      ],
      initialMarking: {work: [{type: "task", data: {id: 1}}]},
    });
    const cons = net({
      name: "consumer",
      places: [
        {name: "in", accepts: ["task"], port: {direction: "input", type: "task"}},
        {name: "done", accepts: ["task"]},
      ],
      transitions: [{name: "consume", handler: "consume_handler"}],
      arcs: [
        {from: {place: "in"}, to: {transition: "consume"}, consume: {type: "task"}},
        {
          from: {transition: "consume"},
          to: {place: "done"},
          produce: {type: "task", destination: "done"},
        },
      ],
      initialMarking: {done: [{type: "task", data: {id: 2}}]},
    });

    // when: merging
    const result = mergeNets(
      new Map([
        ["prod", prod],
        ["cons", cons],
      ]),
      [WIRE],
    );

    // then: marking keys are qualified
    const marking = result.initialMarking;
    expect(marking?.["prod.work"]?.[0]?.data.id).toBe(1);
    expect(marking?.["cons.done"]?.[0]?.data.id).toBe(2);
    // and: the unmarked fused place has no tokens
    expect(marking?.["prod.out"] ?? []).toHaveLength(0);
  });

  it("merges tokens landing on the same fused place from both sides", () => {
    // given: both ports carry initial tokens
    const prod = net({
      name: "producer",
      places: [
        {name: "work", accepts: ["task"]},
        {name: "out", accepts: ["task"], port: {direction: "output", type: "task"}},
      ],
      transitions: [{name: "produce", handler: "produce_handler"}],
      arcs: [
        {from: {place: "work"}, to: {transition: "produce"}, consume: {type: "task"}},
        {
          from: {transition: "produce"},
          to: {place: "out"},
          produce: {type: "task", destination: "out"},
        },
      ],
      initialMarking: {out: [{type: "task", data: {side: "prod"}}]},
    });
    const cons = net({
      name: "consumer",
      places: [
        {name: "in", accepts: ["task"], port: {direction: "input", type: "task"}},
        {name: "done", accepts: ["task"]},
      ],
      transitions: [{name: "consume", handler: "consume_handler"}],
      arcs: [
        {from: {place: "in"}, to: {transition: "consume"}, consume: {type: "task"}},
        {
          from: {transition: "consume"},
          to: {place: "done"},
          produce: {type: "task", destination: "done"},
        },
      ],
      initialMarking: {in: [{type: "task", data: {side: "cons"}}]},
    });

    // when: merging
    const result = mergeNets(
      new Map([
        ["prod", prod],
        ["cons", cons],
      ]),
      [WIRE],
    );

    // then: the fused place holds tokens from both sides
    const fused = result.initialMarking?.["prod.out"] ?? [];
    expect(fused).toHaveLength(2);
    expect(new Set(fused.map((token) => token.data.side))).toEqual(
      new Set(["prod", "cons"]),
    );
  });
});

// ── M10: mergeComposition wrapper ────────────────────────────────────────

describe("mergeComposition (M10)", () => {
  it("delegates to mergeNets using the document's wires", () => {
    // given: a composition document and its resolved nets
    const document: CompositionDocument = {
      nets: [
        {ref: "producer.petrinet", alias: "prod"},
        {ref: "consumer.petrinet", alias: "cons"},
      ],
      wires: [WIRE],
    };
    const aliasToNet = new Map([
      ["prod", producer()],
      ["cons", consumer()],
    ]);

    // when: merging via the wrapper and directly
    const viaWrapper = mergeComposition(document, aliasToNet);
    const direct = mergeNets(aliasToNet, document.wires);

    // then: the two agree structurally
    expect(viaWrapper).toEqual(direct);
  });
});
