import {describe, expect, it} from "vitest";
import {DepositViolationError, Engine} from "../../src/engine/index.js";
import {InMemoryJournal} from "../../src/journal/index.js";
import {HandlerRegistry} from "../../src/registry/index.js";
import type {Marking, Net} from "../../src/schema/types.js";
import {completed, parsedNet, token} from "./helpers.js";

// The Reisig vending-machine counter: sell consumes the counter token and
// re-produces it decremented. The produce cel is ADR 0023's motivating shape.
const DECREMENT = '{"n": binding.counter[0].n - 1}';

function counterNet(cel: string = DECREMENT): Net {
  return parsedNet({
    name: "counter-net",
    places: [{name: "counter", accepts: ["count"]}],
    transitions: [{name: "sell", handler: "sell"}],
    arcs: [
      {from: {place: "counter"}, to: {transition: "sell"}, consume: {type: "count"}},
      {from: {transition: "sell"}, to: {place: "counter"}, produce: {type: "count", destination: "counter", cel}},
    ],
  });
}

function noOutputRegistry(transition = "sell"): HandlerRegistry {
  const registry = new HandlerRegistry();
  registry.registerTransition(transition, () => completed());
  return registry;
}

describe("Engine computed produce fallback (ADR 0023)", () => {
  it("emits the evaluated object as the fallback token's data", () => {
    // given: the counter net, a no-output handler, and n=5 on the counter
    const net = counterNet();
    const engine = new Engine(noOutputRegistry());
    const marking: Marking = {counter: [token("count", {n: 5})]};

    // when: firing sell
    const result = engine.fire(net, marking, "sell", {attempt: 0});

    // then: the consumed token is replaced by the computed one
    expect(result.record.status).toBe("completed");
    expect(result.marking.counter).toEqual([token("count", {n: 4})]);
  });

  it("evaluates over the place-keyed binding across consume and read arcs", () => {
    // given: a join net — consume from orders and rates, read from config
    const net = parsedNet({
      name: "join-net",
      places: [
        {name: "orders", accepts: ["order"]},
        {name: "rates", accepts: ["rate"]},
        {name: "config", accepts: ["config"]},
        {name: "totals", accepts: ["total"]},
      ],
      transitions: [{name: "price", handler: "price"}],
      arcs: [
        {from: {place: "orders"}, to: {transition: "price"}, consume: {type: "order"}},
        {from: {place: "rates"}, to: {transition: "price"}, consume: {type: "rate"}},
        {from: {place: "config"}, to: {transition: "price"}, consume: {type: "config", mode: "read"}},
        {
          from: {transition: "price"},
          to: {place: "totals"},
          produce: {
            type: "total",
            destination: "totals",
            cel: '{"amount": binding.orders[0].qty * binding.rates[0].per_unit + binding.config[0].fee}',
          },
        },
      ],
    });
    const engine = new Engine(noOutputRegistry("price"));
    const marking: Marking = {
      orders: [token("order", {qty: 3})],
      rates: [token("rate", {per_unit: 10})],
      config: [token("config", {fee: 7})],
    };

    // when: firing price
    const result = engine.fire(net, marking, "price", {attempt: 0});

    // then: all three places resolved in the binding environment
    expect(result.record.status).toBe("completed");
    expect(result.marking.totals).toEqual([token("total", {amount: 37})]);
    // and: the read token was not consumed
    expect(result.marking.config).toEqual([token("config", {fee: 7})]);
  });

  it("suppresses the computed fallback when the handler covers the pair", () => {
    // given: the counter net with a handler that supplies the pair itself
    const net = counterNet();
    const registry = new HandlerRegistry();
    registry.registerTransition("sell", () => completed({counter: [token("count", {n: 99})]}));
    const engine = new Engine(registry);
    const marking: Marking = {counter: [token("count", {n: 5})]};

    // when: firing sell
    const result = engine.fire(net, marking, "sell", {attempt: 0});

    // then: only the handler token lands — no computed sibling
    expect(result.marking.counter).toEqual([token("count", {n: 99})]);
  });

  it("still emits a literal-data template alongside a cel template", () => {
    // given: a net with one cel template and one literal template
    const net = parsedNet({
      name: "mixed-net",
      places: [
        {name: "counter", accepts: ["count"]},
        {name: "audit", accepts: ["mark"]},
      ],
      transitions: [{name: "sell", handler: "sell"}],
      arcs: [
        {from: {place: "counter"}, to: {transition: "sell"}, consume: {type: "count"}},
        {from: {transition: "sell"}, to: {place: "counter"}, produce: {type: "count", destination: "counter", cel: DECREMENT}},
        {from: {transition: "sell"}, to: {place: "audit"}, produce: {type: "mark", destination: "audit", data: {fixed: true}}},
      ],
    });
    const engine = new Engine(noOutputRegistry());
    const marking: Marking = {counter: [token("count", {n: 5})]};

    // when: firing sell
    const result = engine.fire(net, marking, "sell", {attempt: 0});

    // then: both fallbacks emit — computed and literal
    expect(result.marking.counter).toEqual([token("count", {n: 4})]);
    expect(result.marking.audit).toEqual([token("mark", {fixed: true})]);
  });

  it("treats a cel eval error as a deposit violation with atomic rollback", () => {
    // given: a counter token missing the field the cel dereferences
    const net = counterNet();
    const engine = new Engine(noOutputRegistry());
    const marking: Marking = {counter: [token("count", {wrong_field: 5})]};

    // when/then: firing raises DepositViolationError naming the destination
    expect(() => engine.fire(net, marking, "sell", {attempt: 0}))
      .toThrowError(/produce cel into "counter" failed to evaluate/u);
    expect(() => engine.fire(net, marking, "sell", {attempt: 0}))
      .toThrow(DepositViolationError);
    // and: the marking is untouched (atomicity)
    expect(marking.counter).toEqual([token("count", {wrong_field: 5})]);
  });

  it("treats a non-object cel result as a deposit violation", () => {
    // given: a cel evaluating to a bare integer
    const net = counterNet("binding.counter[0].n - 1");
    const engine = new Engine(noOutputRegistry());
    const marking: Marking = {counter: [token("count", {n: 5})]};

    // when/then: firing raises DepositViolationError naming the result kind
    expect(() => engine.fire(net, marking, "sell", {attempt: 0}))
      .toThrowError(/produce cel into "counter" must yield a JSON object/u);
  });

  it("records and drops a cel violation under record_then_drop with the marking unchanged", () => {
    // given: an engine with a journal and the drop mode
    const net = counterNet();
    const journal = new InMemoryJournal();
    const engine = new Engine(noOutputRegistry(), {
      journal,
      depositViolation: "record_then_drop",
    });
    const marking: Marking = {counter: [token("count", {wrong_field: 5})]};

    // when: firing with the eval-error-provoking token
    const result = engine.fire(net, marking, "sell", {attempt: 0});

    // then: the violation is recorded, no raise, marking unchanged
    expect(result.marking).toBe(marking);
    expect(result.record).toEqual(expect.objectContaining({
      status: "failed",
      inputTokens: {},
      outputTokens: {},
      error: expect.objectContaining({type: "DepositViolation"}),
    }));
    expect(result.record.error?.message).toContain('produce cel into "counter"');
    expect(journal.records).toEqual([{...result.record, sequence: 0}]);
  });
});
