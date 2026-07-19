import {execFileSync} from "node:child_process";
import {readFileSync} from "node:fs";
import {fileURLToPath} from "node:url";
import {describe, expect, it} from "vitest";
import {Engine} from "../../src/engine/engine.js";
import {InMemoryJournal} from "../../src/journal/memory.js";
import {HandlerRegistry} from "../../src/registry/registry.js";
import {parseNet} from "../../src/schema/parse.js";

const ROOT = fileURLToPath(new URL("../../../../", import.meta.url));
const EXAMPLES = `${ROOT}/examples/contrived`;
const PYTHON_SOURCE = `${ROOT}/implementations/python/src`;
const CASES = ["on_off", "inhibitor_arc", "pull", "cheese_sandwich", "cookie_vending"];

const PYTHON_TRACE = String.raw`
import dataclasses
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

sys.path.insert(0, sys.argv[1])
sys.path.insert(0, sys.argv[2])
from handlers import register_all
from velocitron.engine import Engine
from velocitron.journal import JsonlJournal
from velocitron.parser import parse_net
from velocitron.registry import HandlerRegistry


def plain(value):
    if dataclasses.is_dataclass(value):
        return plain(dataclasses.asdict(value))
    if isinstance(value, Mapping):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [plain(item) for item in value]
    return value


def without_timestamp(record):
    projected = plain(record)
    projected.pop("timestamps", None)
    return projected

net = parse_net(Path(sys.argv[3]))
case = json.loads(Path(sys.argv[4]).read_text())
registry = HandlerRegistry()
register_all(registry)
journal = JsonlJournal()
engine = Engine(registry, journal=journal, deposit_violation="raise")
marking = net.initial_marking
steps = []
for attempt, expected in enumerate(case["expectedFiringSequence"]):
    enabled = engine.enabled_transitions(net, marking, attempt=attempt)
    transition = expected["transition"]
    binding = engine.select_binding(net, transition, marking, attempt=attempt)
    marking, record = engine.fire(net, marking, transition, attempt=attempt)
    steps.append({
        "enabled": enabled,
        "selectedBinding": plain(binding),
        "postMarking": plain(marking),
        "record": without_timestamp(record),
    })

print(json.dumps({
    "steps": steps,
    "finalMarking": plain(marking),
    "journal": [without_timestamp(record) for record in journal._records],
}))
`;

function completed(outputTokens = {}) {
  return {status: "completed", outputTokens, error: null, metadata: {}};
}

function registerContrivedHandlers(registry) {
  const passthrough = (destination, source) => ({inputTokens}) => completed({
    [destination]: inputTokens[source] ?? [],
  });
  registry.registerTransition("turn_on", passthrough("on", "off"));
  registry.registerTransition("unlock", passthrough("unlocked", "locked"));
  registry.registerTransition("run", passthrough("done", "waiting"));
  registry.registerTransition("prep_a", passthrough("preparing_a", "ready_a"));
  registry.registerTransition("work_a", passthrough("working_a", "preparing_a"));
  registry.registerTransition("done_a", ({inputTokens}) => completed({
    product_a: inputTokens.working_a ?? [],
    ready_a: inputTokens.working_a ?? [],
  }));
  registry.registerTransition("prep_b", ({inputTokens}) => completed({
    preparing_b: inputTokens.ready_b ?? [],
    demand_a: inputTokens.demand_b ?? [],
  }));
  registry.registerTransition("work_b", passthrough("working_b", "preparing_b"));
  registry.registerTransition("done_b", ({inputTokens}) => completed({
    product_b: inputTokens.working_b ?? [],
    ready_b: inputTokens.working_b ?? [],
  }));
  registry.registerTransition("prep_c", ({inputTokens}) => completed({
    preparing_c: inputTokens.ready_c ?? [],
    demand_b: inputTokens.demand_c ?? [],
  }));
  registry.registerTransition("work_c", passthrough("working_c", "preparing_c"));
  registry.registerTransition("done_c", ({inputTokens}) => completed({
    product_c: inputTokens.working_c ?? [],
    ready_c: inputTokens.working_c ?? [],
  }));
  registry.registerTransition("receive_c", passthrough("received", "product_c"));
  registry.registerGuard("enough_cheese_left", ({inputTokens}) => (
    inputTokens.cheese_block[0].data.thickness_mm >= 2
  ));
  registry.registerTransition("slice_cheese", ({inputTokens}) => {
    const block = inputTokens.cheese_block[0];
    return completed({cheese_block: [{
      ...block,
      data: {...block.data, thickness_mm: block.data.thickness_mm - 2},
    }]});
  });
  registry.registerTransition("layer", () => completed());
  registry.registerTransition("eat_cheese_sandwich", () => completed());
  registry.registerTransition("see_mold", passthrough("moldy_bread_slices", "bread_slices"));
  registry.registerTransition("see_no_mold", passthrough("edible_bread_slices", "bread_slices"));
  registry.registerTransition("compost", () => completed());
  registry.registerTransition("accept_coin", passthrough("cash_box", "coin_slot"));
  registry.registerTransition("vend_packet", passthrough("compartment", "storage"));
  registry.registerTransition("return_coin", () => completed());
  registry.registerTransition("take_packet", () => completed());
}

function withoutTimestamp(record) {
  const {timestamps: _excludedOnlyField, ...projected} = record;
  return projected;
}

function nonempty(marking) {
  return Object.fromEntries(Object.entries(marking).filter(([, tokens]) => tokens.length > 0));
}

function pythonTrace(name) {
  return JSON.parse(execFileSync("python3", [
    "-c",
    PYTHON_TRACE,
    PYTHON_SOURCE,
    EXAMPLES,
    `${EXAMPLES}/${name}.json`,
    `${EXAMPLES}/${name}.test.json`,
  ], {encoding: "utf8"}));
}

function typescriptTrace(netDocument, scenario) {
  const net = parseNet(netDocument);
  const registry = new HandlerRegistry();
  registerContrivedHandlers(registry);
  const journal = new InMemoryJournal();
  const engine = new Engine(registry, {
    journal,
    depositViolation: "raise",
    clock: {now: () => "excluded"},
  });
  let marking = net.initialMarking ?? {};
  const steps = [];
  scenario.expectedFiringSequence.forEach((expected, attempt) => {
    const enabled = engine.enabledTransitions(net, marking, {attempt});
    const selectedBinding = engine.selectBinding(net, expected.transition, marking, {attempt});
    const fired = engine.fire(net, marking, expected.transition, {attempt});
    marking = fired.marking;
    steps.push({
      enabled,
      selectedBinding,
      postMarking: marking,
      record: withoutTimestamp(fired.record),
    });
  });
  return {
    steps,
    finalMarking: marking,
    journal: journal.records.map(withoutTimestamp),
  };
}

describe("shared contrived cross-language parity", () => {
  for (const name of CASES) {
    it(`${name} matches Python for bindings, markings, records, and journal order`, () => {
      // given: the shared canonical net and expected scenario consumed by both ports
      const netDocument = JSON.parse(readFileSync(`${EXAMPLES}/${name}.json`, "utf8"));
      const scenario = JSON.parse(readFileSync(`${EXAMPLES}/${name}.test.json`, "utf8"));

      // when: Python and TypeScript execute the exact expected transition sequence
      const python = pythonTrace(name);
      const typescript = typescriptTrace(netDocument, scenario);

      // then: every observable field matches; timestamps are the one exact exclusion
      // Bite: this compares enabled sets, selected bindings, every post marking,
      // complete FiringRecords, deposited tokens, errors/status, and journal sequence.
      expect(typescript).toEqual(python);
      expect(nonempty(typescript.finalMarking)).toEqual(scenario.expectedFinalMarking);
      expect(typescript.journal.map(({transition, status, sequence}) => ({
        transition,
        status,
        sequence,
      }))).toEqual(scenario.expectedFiringSequence.map((expected, sequence) => ({
        ...expected,
        sequence,
      })));
    });
  }
});
