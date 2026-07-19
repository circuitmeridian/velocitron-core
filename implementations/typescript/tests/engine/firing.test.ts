import {describe, expect, it} from "vitest";
import {Engine, DepositViolationError, UnknownTransitionError} from "../../src/engine/index.js";
import {InMemoryJournal} from "../../src/journal/index.js";
import {HandlerRegistry} from "../../src/registry/index.js";
import type {Marking} from "../../src/schema/types.js";
import {completed, failed, nonempty, parsedNet, token} from "./helpers.js";

const PASSTHROUGH_NET = parsedNet({
  name: "sharing",
  places: [
    {name: "input", accepts: ["item"]},
    {name: "read", accepts: ["control"]},
    {name: "output", accepts: ["item", "audit"]},
    {name: "untouched", accepts: ["stable"], capacityPerColorKey: {key: "id", max: 1}},
  ],
  transitions: [{name: "move", handler: "move"}],
  arcs: [
    {from: {place: "input"}, to: {transition: "move"}, consume: {type: "item", mode: "consume", weight: 1}},
    {from: {place: "read"}, to: {transition: "move"}, consume: {type: "control", mode: "read", weight: 1}},
    {from: {transition: "move"}, to: {place: "output"}, produce: {type: "item", destination: "output"}},
    {from: {transition: "move"}, to: {place: "output"}, produce: {type: "audit", destination: "output", data: {fixed: 1}}},
  ],
  initialMarking: {},
});

function passthroughRegistry(): HandlerRegistry {
  const registry = new HandlerRegistry();
  registry.registerTransition("move", ({inputTokens}) => completed({output: inputTokens.input}));
  return registry;
}

describe("Engine firing", () => {
  it("commits consume, read, handler output, and literal fallback atomically with structural sharing", () => {
    // given: consume/read inputs, an existing destination token, and an unrelated place
    const consumed = token("item", {id: "A"});
    const control = token("control", {gate: true});
    const existing = token("item", {id: "old"});
    const stable = token("stable", {id: "same"});
    const marking: Marking = {
      input: [consumed],
      read: [control],
      output: [existing],
      untouched: [stable],
    };
    const journal = new InMemoryJournal();
    const engine = new Engine(passthroughRegistry(), {
      journal,
      clock: {now: () => "2026-07-16T00:00:00.000Z"},
      idFactory: {
        firingId: ({attempt}) => `fire-${attempt}`,
        injectionId: ({attempt}) => `inject-${attempt}`,
      },
    });

    // when: the enabled transition fires
    const selected = engine.selectBinding(PASSTHROUGH_NET, "move", marking, {attempt: 7});
    const result = engine.fire(PASSTHROUGH_NET, marking, "move", {attempt: 7});

    // then: read tokens stay, only changed arrays are copied, and the record is complete
    expect(selected).toEqual({input: [consumed], read: [control]});
    expect(result.marking).not.toBe(marking);
    expect(result.marking.input).not.toBe(marking.input);
    expect(result.marking.output).not.toBe(marking.output);
    expect(result.marking.read).toBe(marking.read);
    expect(result.marking.untouched).toBe(marking.untouched);
    expect(result.marking).toEqual({
      input: [],
      read: [control],
      output: [existing, consumed, token("audit", {fixed: 1})],
      untouched: [stable],
    });
    expect(result.record).toEqual({
      firingId: "fire-7",
      netId: "sharing",
      transition: "move",
      attempt: 7,
      status: "completed",
      inputTokens: selected,
      outputTokens: {output: [consumed, token("audit", {fixed: 1})]},
      error: null,
      metadata: {},
      timestamps: {fired_at: "2026-07-16T00:00:00.000Z"},
    });
    expect(journal.records).toEqual([{...result.record, sequence: 0}]);
  });

  it("returns the exact input marking for every declared firing failure", () => {
    // given: disabled, missing, and explicitly failing transition variants
    const net = parsedNet({
      name: "failures",
      places: [{name: "work", accepts: ["job"]}],
      transitions: [
        {name: "disabled", handler: "ok"},
        {name: "handlerless"},
        {name: "missing", handler: "missing"},
        {name: "failed", handler: "failed"},
      ],
      arcs: ["disabled", "handlerless", "missing", "failed"].map((name) => ({
        from: {place: "work"},
        to: {transition: name},
        consume: {type: "job", mode: "consume", weight: 1},
      })),
    });
    const registry = new HandlerRegistry();
    registry.registerTransition("ok", () => completed());
    registry.registerTransition("handlerless", () => completed());
    registry.registerTransition("failed", () => failed("Expected", "no change"));
    const engine = new Engine(registry);
    const empty: Marking = {work: []};
    const ready: Marking = {work: [token("job", {nested: {unhashable: true}})]};

    // when: each failure path is fired
    const disabled = engine.fire(net, empty, "disabled", {attempt: 0});
    const handlerless = engine.fire(net, ready, "handlerless", {attempt: 1});
    const missing = engine.fire(net, ready, "missing", {attempt: 2});
    const declared = engine.fire(net, ready, "failed", {attempt: 3});

    // then: precedence and error records are exact, with no tentative mutation
    // Bite: registering a same-name handler must never make a handlerless transition run.
    expect(disabled.marking).toBe(empty);
    expect(disabled.record.error?.type).toBe("NotEnabled");
    expect(handlerless.marking).toBe(ready);
    expect(handlerless.record.error).toEqual(expect.objectContaining({type: "HandlerNotFound"}));
    expect(handlerless.record.error?.message).toContain("has no handler");
    expect(missing.marking).toBe(ready);
    expect(missing.record.error).toEqual(expect.objectContaining({type: "HandlerNotFound"}));
    expect(declared.marking).toBe(ready);
    expect(declared.record).toEqual(expect.objectContaining({
      status: "failed",
      inputTokens: {},
      outputTokens: {},
      error: {type: "Expected", message: "no change"},
      metadata: {observed: true},
    }));
    expect(ready.work).toEqual([token("job", {nested: {unhashable: true}})]);
  });

  it("raises for an unknown transition before source-transition binding can be invented", () => {
    // given: a valid source transition and an undeclared name
    const net = parsedNet({
      name: "source",
      places: [],
      transitions: [{name: "declared", handler: "declared"}],
      arcs: [],
    });
    const registry = new HandlerRegistry();
    registry.registerTransition("declared", () => completed());
    const engine = new Engine(registry);

    // when/then: both name-taking APIs reject the typo with typed net/name details
    expect(() => engine.selectBinding(net, "typo", {})).toThrowError(expect.objectContaining({
      net: "source",
      transition: "typo",
    }));
    expect(() => engine.fire(net, {}, "typo", {attempt: 0})).toThrow(UnknownTransitionError);
  });

  it("validates only declared handler references at the opt-in boundary", () => {
    // given: a handlerless transition plus declared transition, guard, and predicate refs
    const net = parsedNet({
      name: "validate",
      places: [{name: "in", accepts: ["item"]}],
      transitions: [
        {name: "structure-only"},
        {name: "go", handler: "go", guard: "guard"},
      ],
      arcs: [{
        from: {place: "in"},
        to: {transition: "go"},
        consume: {
          type: "item",
          mode: "consume",
          weight: 1,
          predicate: {handler: "predicate"},
        },
      }],
    });
    const registry = new HandlerRegistry();
    registry.registerTransition("go", () => completed());
    registry.registerGuard("guard", () => true);
    const engine = new Engine(registry);

    // when/then: absence on structure-only is skipped, while the first declared miss raises
    expect(() => engine.validate(net)).toThrowError(expect.objectContaining({
      kind: "predicate",
      handlerName: "predicate",
    }));
    registry.registerPredicate("predicate", () => true);
    expect(() => engine.validate(net)).not.toThrow();
  });

  it("preserves every parallel produce template and applies handler-token precedence per destination/type pair", () => {
    // given: parallel templates sharing a destination, including duplicate literal fallbacks
    const net = parsedNet({
      name: "parallel",
      places: [{name: "out", accepts: ["a", "b"]}],
      transitions: [{name: "emit", handler: "emit"}],
      arcs: [
        {from: {transition: "emit"}, to: {place: "out"}, produce: {type: "a", destination: "out", data: {fallback: 1}}},
        {from: {transition: "emit"}, to: {place: "out"}, produce: {type: "b", destination: "out", data: {fallback: 2}}},
        {from: {transition: "emit"}, to: {place: "out"}, produce: {type: "b", destination: "out", data: {fallback: 3}}},
      ],
    });
    const supplied = token("a", {handler: true});
    const registry = new HandlerRegistry();
    registry.registerTransition("emit", () => completed({out: [supplied]}));

    // when: B02 deposit executes
    const result = new Engine(registry).fire(net, {}, "emit", {attempt: 0});

    // then: the handler a replaces only a literals; both declaration-ordered b literals remain
    expect(result.record.outputTokens).toEqual({out: [
      supplied,
      token("b", {fallback: 2}),
      token("b", {fallback: 3}),
    ]});
    expect(result.marking.out).toEqual(result.record.outputTokens.out);
  });

  it("rolls back wrong destination/type deposits under all configured violation policies", () => {
    // given: a completed handler that violates its only produce contract
    const net = parsedNet({
      name: "violation",
      places: [
        {name: "in", accepts: ["job"]},
        {name: "out", accepts: ["ok", "wrong"]},
      ],
      transitions: [{name: "go", handler: "bad"}],
      arcs: [
        {from: {place: "in"}, to: {transition: "go"}, consume: {type: "job", mode: "consume", weight: 1}},
        {from: {transition: "go"}, to: {place: "out"}, produce: {type: "ok", destination: "out"}},
      ],
    });
    const registry = new HandlerRegistry();
    registry.registerTransition("bad", () => completed({out: [token("wrong")]}));
    const marking: Marking = {in: [token("job")]};

    // when/then: raise has no journal record; record/drop uses the violation-only channel
    expect(() => new Engine(registry).fire(net, marking, "go", {attempt: 0}))
      .toThrow(DepositViolationError);
    const dropJournal = new InMemoryJournal();
    const dropped = new Engine(registry, {
      journal: dropJournal,
      depositViolation: "record_then_drop",
    }).fire(net, marking, "go", {attempt: 1});
    expect(dropped.marking).toBe(marking);
    expect(dropped.record).toEqual(expect.objectContaining({
      status: "failed",
      inputTokens: {},
      outputTokens: {},
      error: expect.objectContaining({type: "DepositViolation"}),
    }));
    expect(dropJournal.records).toEqual([{...dropped.record, sequence: 0}]);

    const raiseJournal = new InMemoryJournal();
    expect(() => new Engine(registry, {
      journal: raiseJournal,
    }).fire(net, marking, "go", {attempt: 2})).toThrow(DepositViolationError);
    expect(raiseJournal.records).toHaveLength(1);
    expect(raiseJournal.records[0]).toEqual(expect.objectContaining({
      error: expect.objectContaining({type: "DepositViolation"}),
      sequence: 0,
    }));
  });

  it("injects and batch-injects atomically while sharing untouched place arrays", () => {
    // given: two injectable places and a single journal stream
    const net = parsedNet({
      name: "inject",
      places: [
        {name: "events", accepts: ["event"]},
        {name: "clock", accepts: ["clock"]},
        {name: "other", accepts: ["stable"]},
      ],
      transitions: [],
      arcs: [],
    });
    const journal = new InMemoryJournal();
    const engine = new Engine(new HandlerRegistry(), {
      journal,
      clock: {now: () => "fixed"},
    });
    const oldClock = token("clock", {now: 1});
    const stable = [token("stable")];
    const marking: Marking = {events: [], clock: [oldClock], other: stable};

    // when: append, replace, and batch paths are used
    const appended = engine.inject(net, marking, "events", token("event", {n: 1}), {attempt: 4});
    const replaced = engine.inject(net, appended.marking, "clock", token("clock", {now: 2}), {
      attempt: 5,
      replace: true,
    });
    const batch = engine.injectMany(net, replaced.marking, [
      {place: "events", token: token("event", {n: 2})},
      {place: "events", token: token("event", {n: 3})},
    ], {attempt: 6});

    // then: only touched arrays change and injection/firing channels share contiguous sequence
    expect(appended.marking.other).toBe(stable);
    expect(replaced.record).toEqual(expect.objectContaining({
      kind: "update",
      replaced: [oldClock],
      tokens: [token("clock", {now: 2})],
    }));
    expect(batch.marking.events).toEqual([
      token("event", {n: 1}),
      token("event", {n: 2}),
      token("event", {n: 3}),
    ]);
    expect(batch.marking.other).toBe(stable);
    expect(journal.records.map((record) => record.sequence)).toEqual([0, 1, 2, 3]);

    // and: batch validation is all-or-nothing, including journal side effects
    expect(() => engine.injectMany(net, batch.marking, [
      {place: "events", token: token("event", {n: 4})},
      {place: "missing", token: token("event", {n: 5})},
    ], {attempt: 7})).toThrowError(/no place named/);
    expect(journal.records).toHaveLength(4);
  });

  it("keeps capacity declarations non-behavioral during deposits", () => {
    // given: a place whose declarative verification capacity is already occupied
    const net = parsedNet({
      name: "capacity-is-not-a-gate",
      places: [{
        name: "out",
        accepts: ["item"],
        capacityPerColorKey: {key: "group", max: 1},
      }],
      transitions: [{name: "emit", handler: "emit"}],
      arcs: [{
        from: {transition: "emit"},
        to: {place: "out"},
        produce: {type: "item", destination: "out"},
      }],
    });
    const registry = new HandlerRegistry();
    registry.registerTransition("emit", () => completed({out: [token("item", {group: "A"})]}));

    // when: another same-key token is deposited
    const result = new Engine(registry).fire(
      net,
      {out: [token("item", {group: "A"})]},
      "emit",
      {attempt: 0},
    );

    // then: Engine does not turn the verification-only declaration into enablement behavior
    expect(result.marking.out).toHaveLength(2);
  });
  it("preserves reserved JSON keys across binding, deposit, marking, injection, and policy records", () => {
    // given: valid semantic names that collide with Object.prototype accessors
    const reserved = "__proto__";
    const net = parsedNet({
      name: "prototype-safe",
      places: [{name: reserved, accepts: ["item"]}],
      transitions: [{name: reserved, handler: "reserved", priority: 1}],
      arcs: [
        {
          from: {place: reserved},
          to: {transition: reserved},
          consume: {type: "item", mode: "consume", weight: 1},
        },
        {
          from: {transition: reserved},
          to: {place: reserved},
          produce: {type: "item", destination: reserved},
        },
      ],
    });
    const original = token("item", {id: "before"});
    const replacement = token("item", {id: "after"});
    const marking: Marking = Object.fromEntries([[reserved, [original]]]);
    const registry = new HandlerRegistry();
    registry.registerTransition("reserved", ({inputTokens}) => {
      expect(Object.hasOwn(inputTokens, reserved)).toBe(true);
      return completed(Object.fromEntries([[reserved, [replacement]]]));
    });
    const engine = new Engine(registry, {policy: "priority"});

    // when: the reserved-name transition binds, fires, runs, and receives injection
    const binding = engine.selectBinding(net, reserved, marking);
    const fired = engine.fire(net, marking, reserved, {attempt: 0});
    const run = engine.run(net, marking, {maxSteps: 1});
    const injected = engine.inject(net, fired.marking, reserved, original, {attempt: 1});

    // then: every arbitrary-name accumulator creates an own key on a null-prototype record
    // Bite: `{}` plus assignment either throws on binding or silently loses this deposit.
    expect(Object.hasOwn(binding!, reserved)).toBe(true);
    expect(Object.hasOwn(fired.record.outputTokens, reserved)).toBe(true);
    expect(Object.hasOwn(fired.marking, reserved)).toBe(true);
    expect(Object.hasOwn(run, reserved)).toBe(true);
    expect(Object.hasOwn(injected.marking, reserved)).toBe(true);
    expect(Object.getPrototypeOf(fired.marking)).toBeNull();
    expect(fired.marking[reserved]).toEqual([replacement]);
    expect(injected.marking[reserved]).toEqual([replacement, original]);
  });

});

describe("Engine run loop", () => {
  it("honors maxSteps, policy null, priority selection, and consecutive failure exhaustion", () => {
    // given: a failing first transition and completing sibling over independent inputs
    const net = parsedNet({
      name: "loop",
      places: [
        {name: "a", accepts: ["token"]},
        {name: "b", accepts: ["token"]},
      ],
      transitions: [
        {name: "fail", handler: "fail", priority: 0},
        {name: "complete", handler: "complete", priority: 10},
      ],
      arcs: [
        {from: {place: "a"}, to: {transition: "fail"}, consume: {type: "token", mode: "consume", weight: 1}},
        {from: {place: "b"}, to: {transition: "complete"}, consume: {type: "token", mode: "consume", weight: 1}},
      ],
    });
    const registry = new HandlerRegistry();
    registry.registerTransition("fail", () => failed());
    registry.registerTransition("complete", () => completed());
    const start: Marking = {a: [token("token")], b: [token("token")]};

    // when/then: zero steps returns the same marking and priority chooses the completing sibling
    expect(new Engine(registry).run(net, start, {maxSteps: 0})).toBe(start);
    const priorityJournal = new InMemoryJournal();
    const priorityResult = new Engine(registry, {policy: "priority", journal: priorityJournal})
      .run(net, start, {maxSteps: 1});
    expect(nonempty(priorityResult)).toEqual({a: [token("token")]});
    expect(priorityJournal.records.map((record) => "transition" in record && record.transition))
      .toEqual(["complete"]);

    // and: the budget exhausts the failing first-found choice so the sibling runs
    const budgetJournal = new InMemoryJournal();
    const budgetResult = new Engine(registry, {
      journal: budgetJournal,
      maxConsecutiveFailures: 2,
    }).run(net, start, {maxSteps: 10});
    expect(nonempty(budgetResult)).toEqual({a: [token("token")]});
    expect(budgetJournal.records.map((record) => "transition" in record && record.transition))
      .toEqual(["fail", "fail", "complete", "fail", "fail"]);

    // and: a registered policy may return null without firing or allocating a marking
    registry.registerFiringPolicy("stop", () => null);
    expect(new Engine(registry, {policy: "stop"}).run(net, start)).toBe(start);
  });
});
