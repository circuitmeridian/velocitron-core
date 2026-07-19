import {describe, expect, it} from "vitest";
import {Engine} from "../../src/engine/index.js";
import {InMemoryJournal} from "../../src/journal/index.js";
import {HandlerRegistry} from "../../src/registry/index.js";
import type {Marking} from "../../src/schema/types.js";
import {completed, parsedNet, token} from "./helpers.js";

describe("binding, conditions, and inhibitors", () => {
  it("selects declaration/token-order lexicographic combinations with equality-based multiset accounting", () => {
    // given: two same-place weighted arcs and JSON-object token data
    const net = parsedNet({
      name: "lexicographic",
      places: [{name: "items", accepts: ["item"]}],
      transitions: [{name: "pair", handler: "pair", guard: "accept-later"}],
      arcs: [
        {from: {place: "items"}, to: {transition: "pair"}, consume: {type: "item", mode: "consume", weight: 1}},
        {from: {place: "items"}, to: {transition: "pair"}, consume: {type: "item", mode: "consume", weight: 1}},
      ],
    });
    const first = token("item", {id: "first", nested: {value: 1}});
    const second = token("item", {id: "second", nested: {value: 1}});
    const third = token("item", {id: "third", nested: {value: 1}});
    const marking: Marking = {items: [first, second, third]};
    const registry = new HandlerRegistry();
    registry.registerGuard("accept-later", ({inputTokens}) => (
      inputTokens.items?.[0]?.data.id === "first" &&
      inputTokens.items?.[1]?.data.id === "second"
    ));
    registry.registerTransition("pair", () => completed());

    // when: products initially try to reuse the first occurrence, then advance lexicographically
    const binding = new Engine(registry).selectBinding(net, "pair", marking);

    // then: the first valid accepted equality-multiset binding is first + second
    // Bite: object hashing, set membership, or arc-order sorting changes this observable pair.
    expect(binding).toEqual({items: [first, second]});
  });

  it("requires exact true from named predicates and guards without host truthiness", () => {
    // given: truthy non-booleans returned at each named condition surface
    const predicateNet = parsedNet({
      name: "predicate-strict",
      places: [{name: "in", accepts: ["item"]}],
      transitions: [{name: "go", handler: "go"}],
      arcs: [{
        from: {place: "in"},
        to: {transition: "go"},
        consume: {
          type: "item",
          mode: "consume",
          weight: 1,
          predicate: {handler: "truthy"},
        },
      }],
    });
    const guardNet = parsedNet({
      name: "guard-strict",
      places: [],
      transitions: [{name: "go", handler: "go", guard: "truthy"}],
      arcs: [],
    });
    const registry = new HandlerRegistry();
    registry.registerTransition("go", () => completed());
    registry.registerPredicate("truthy", () => 1 as unknown as boolean);
    registry.registerGuard("truthy", () => ({yes: true}) as unknown as boolean);

    // when: enablement evaluates each truthy non-boolean
    const engine = new Engine(registry);

    // then: B03 exact-true rejects both instead of applying JavaScript truthiness
    expect(engine.enabledTransitions(predicateNet, {in: [token("item")]})).toEqual([]);
    expect(engine.enabledTransitions(guardNet, {})).toEqual([]);
  });

  it("filters correlated inhibit candidates in order and fails closed on non-booleans", () => {
    // given: a blocker correlated only to the first job
    const correlated = parsedNet({
      name: "correlated",
      places: [
        {name: "jobs", accepts: ["job"]},
        {name: "locks", accepts: ["lock"]},
      ],
      transitions: [{name: "run", handler: "run"}],
      arcs: [
        {from: {place: "jobs"}, to: {transition: "run"}, consume: {type: "job", mode: "consume", weight: 1}},
        {from: {place: "locks"}, to: {transition: "run"}, consume: {
          type: "lock",
          mode: "inhibit",
          weight: 1,
          correlate: {cel: "token.id == binding.jobs[0].id"},
        }},
      ],
    });
    const failClosed = parsedNet({
      ...correlated,
      name: "fail-closed",
      arcs: [
        correlated.arcs[0],
        {from: {place: "locks"}, to: {transition: "run"}, consume: {
          type: "lock",
          mode: "inhibit",
          weight: 1,
          correlate: {cel: "token.id"},
        }},
      ],
    });
    const registry = new HandlerRegistry();
    registry.registerTransition("run", () => completed());
    const marking = {
      jobs: [token("job", {id: "A"}), token("job", {id: "B"})],
      locks: [token("lock", {id: "A"})],
    };

    // when: binding candidates meet the anti-join
    const engine = new Engine(registry);

    // then: the blocked first binding is skipped, while a non-boolean correlation blocks safely
    expect(engine.selectBinding(correlated, "run", marking)).toEqual({jobs: [token("job", {id: "B"})]});
    expect(engine.selectBinding(failClosed, "run", marking)).toBeNull();
  });

  it("treats a non-boolean CEL predicate as false and a non-boolean inhibit predicate as no match", () => {
    // given: CEL expressions returning an integer rather than boolean
    const net = parsedNet({
      name: "cel-strict",
      places: [
        {name: "input", accepts: ["item"]},
        {name: "blockers", accepts: ["blocker"]},
      ],
      transitions: [
        {name: "ordinary", handler: "ordinary"},
        {name: "inhibited", handler: "inhibited"},
      ],
      arcs: [
        {from: {place: "input"}, to: {transition: "ordinary"}, consume: {
          type: "item", mode: "consume", weight: 1, predicate: {cel: "n"},
        }},
        {from: {place: "blockers"}, to: {transition: "inhibited"}, consume: {
          type: "blocker", mode: "inhibit", weight: 1, predicate: {cel: "n"},
        }},
      ],
    });
    const registry = new HandlerRegistry();
    registry.registerTransition("ordinary", () => completed());
    registry.registerTransition("inhibited", () => completed());

    // when/then: B03 applies with each surface's established failure posture
    expect(new Engine(registry).enabledTransitions(net, {
      input: [token("item", {n: 1})],
      blockers: [token("blocker", {n: 1})],
    })).toEqual(["inhibited"]);
  });
});

describe("timed transitions", () => {
  const timedNet = parsedNet({
    name: "timed",
    places: [
      {name: "clock", accepts: ["clock"]},
      {name: "jobs", accepts: ["job"]},
    ],
    transitions: [{
      name: "expire",
      handler: "expire",
      timer: {
        clock: "clock",
        cel: "clock.now >= job.due",
        bind: {job: "jobs"},
        maturity: "job.due",
      },
    }],
    arcs: [{
      from: {place: "jobs"},
      to: {transition: "expire"},
      consume: {type: "job", mode: "consume", weight: 1},
    }],
  });

  it("reports future maturity and tick composes replacement injection with quiescent run", () => {
    // given: an integral JSON clock before one deadline
    const registry = new HandlerRegistry();
    registry.registerTransition("expire", () => completed());
    const journal = new InMemoryJournal();
    const engine = new Engine(registry, {
      journal,
      clock: {now: () => "fixed"},
    });
    const marking: Marking = {
      clock: [token("clock", {now: 10})],
      jobs: [token("job", {due: 20})],
    };

    // when: advisory maturity is read, then the clock advances beyond it
    const maturities = engine.timerMaturities(timedNet, marking);
    const finalMarking = engine.tick(timedNet, marking, "clock", token("clock", {now: 25}), {
      attempt: 9,
      maxSteps: 10,
    });

    // then: bigint-backed CEL arithmetic normalizes maturity, and injection precedes firing
    expect(maturities).toEqual([{transition: "expire", clock: "clock", at: 20}]);
    expect(finalMarking).toEqual({clock: [token("clock", {now: 25})], jobs: []});
    expect(journal.records.map((record) => record.sequence)).toEqual([0, 1]);
    expect(journal.records[0]).toEqual(expect.objectContaining({kind: "update", attempt: 9}));
    expect(journal.records[1]).toEqual(expect.objectContaining({transition: "expire", attempt: 0}));
  });

  it("requires an exact boolean timer condition and skips invalid maturity results", () => {
    // given: timer expressions whose condition/result have the wrong contracts
    const nonBooleanTimer = parsedNet({
      ...timedNet,
      name: "timer-nonboolean",
      transitions: [{
        name: "expire",
        handler: "expire",
        timer: {clock: "clock", cel: "clock.now", maturity: "job.due", bind: {job: "jobs"}},
      }],
    });
    const invalidMaturity = parsedNet({
      ...timedNet,
      name: "maturity-invalid",
      transitions: [{
        name: "expire",
        handler: "expire",
        timer: {clock: "clock", cel: "false", maturity: "true", bind: {job: "jobs"}},
      }],
    });
    const registry = new HandlerRegistry();
    registry.registerTransition("expire", () => completed());
    const marking = {clock: [token("clock", {now: 10})], jobs: [token("job", {due: 20})]};

    // when/then: condition is false-by-contract and boolean maturity is unschedulable
    const engine = new Engine(registry);
    expect(engine.enabledTransitions(nonBooleanTimer, marking)).toEqual([]);
    expect(engine.timerMaturities(invalidMaturity, marking)).toEqual([]);
  });
});
