// @ts-expect-error -- Node globals are provided by the Vitest process; the
// browser-safe production package intentionally does not depend on @types/node.
import {readFileSync} from "node:fs";
// @ts-expect-error -- See the node:fs import above.
import {fileURLToPath} from "node:url";
import {describe, expect, it} from "vitest";
import {compilePetrinetText} from "../../src/dsl/api.js";
import {lowerPetrinetText, validateContributionIr} from "../../src/dsl/internal.js";

// Minimal timed net exercising the surface under test: every fact below is
// structurally required for a valid `timer maturity` declaration (a timer
// maturity fact requires a timer clock fact, which requires a topology-declared
// clock place; the bind fact names the alias the maturity CEL dereferences).
const TIMED_NET = [
  "net sla",
  "(work) -job-> [escalate] -job-> (done)",
  "(clock) -tick->? [escalate]",
  '[escalate] timer clock (clock) cel "clock.now >= job.dueAt"',
  "[escalate] timer bind job (work)",
  '[escalate] timer maturity cel "job.dueAt"',
  "",
].join("\n");

const kitchenSinkPath = fileURLToPath(
  new URL("../../../../skills/velocitron/kitchen-sink.petrinet", import.meta.url),
);

describe("transition.timer-maturity contribution IR schema coverage", () => {
  it("accepts the lowered transition.timer-maturity contribution", () => {
    // given: the lowered IR of a net carrying a `timer maturity cel` fact
    const ir = lowerPetrinetText(TIMED_NET, "sla.petrinet");

    // then: the lowering emitted the transition.timer-maturity kind
    const kinds = ir.contributions.map((contribution) => contribution.kind);
    expect(kinds).toContain("transition.timer-maturity");

    // and: the IR schema accepts it (red: the kind is absent from the
    // contribution oneOf union, so validation reports schema errors)
    expect(validateContributionIr(ir)).toEqual({ok: true});
  });

  it("compiles a timer maturity fact to the same timer JSON Python produces", () => {
    // when: compiling the timed net through the full TS pipeline
    const compiled = compilePetrinetText(TIMED_NET, "sla.petrinet");

    // then: the resolved transition carries the identical timer object the
    // Python reference pipeline resolves for the same source
    expect(compiled.documentKind).toBe("net");
    expect(compiled.document.transitions).toEqual([
      {
        name: "escalate",
        timer: {
          clock: "clock",
          cel: "clock.now >= job.dueAt",
          bind: {job: "work"},
          maturity: "job.dueAt",
        },
      },
    ]);
  });

  it("compiles the kitchen-sink skill document clean", () => {
    // given: the skill's kitchen-sink document, which uses `timer maturity`
    const text = readFileSync(kitchenSinkPath, "utf8");

    // when/then: the full TS pipeline compiles it without diagnostics
    const compiled = compilePetrinetText(text, "kitchen-sink.petrinet");
    expect(compiled.documentKind).toBe("net");
  });
});
