import {describe, expect, it} from "vitest";
import {
  compilePetrinetText,
  PetrinetDslError,
  PetrinetResourceError,
} from "../../src/dsl/api.js";
import {
  lowerPetrinetText,
  resolveContributionIr,
  validateContributionIr,
  type ContributionIr,
} from "../../src/dsl/internal.js";
import {PETRINET_RESOURCE_LIMITS} from "../../src/dsl/diagnostics.js";

type MutableObject = Record<string, unknown>;

function cloneIr(ir: ContributionIr): MutableObject {
  return structuredClone(ir) as unknown as MutableObject;
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

function captureResource(action: () => unknown): PetrinetResourceError {
  try {
    action();
  } catch (error) {
    expect(error).toBeInstanceOf(PetrinetResourceError);
    return error as PetrinetResourceError;
  }
  throw new Error("expected a Petri-net resource error");
}

describe("public DSL compilation stage", () => {
  it("compiles net JSON without invoking core semantic validation", () => {
    const compiled = compilePetrinetText("net empty\n", "empty.petrinet");

    expect(compiled).toEqual({
      documentKind: "net",
      document: {
        name: "empty",
        places: [],
        transitions: [],
        arcs: [],
        initialMarking: {},
      },
    });
    expect(Object.isFrozen(compiled)).toBe(true);
    expect(Object.isFrozen(compiled.document)).toBe(true);
  });

  it("reports composition kind without loading referenced nets", () => {
    const compiled = compilePetrinetText(
      'composition c\nuse "not-present.petrinet" as absent\n',
      "composition.petrinet",
    );

    expect(compiled).toEqual({
      documentKind: "composition",
      document: {
        nets: [{ref: "not-present.petrinet", alias: "absent"}],
        wires: [],
      },
    });
  });

  it("preserves supplementary scalars, CRLF, and stable source IDs", () => {
    const sourceId = "source-😀.petrinet";
    const ir = lowerPetrinetText('net n "😀"\r\n', sourceId);

    expect(ir.document.id).toBe(sourceId);
    expect(ir.contributions[0]?.span).toEqual({
      source: sourceId,
      start: {byteOffset: 0, line: 1, column: 1},
      end: {byteOffset: 12, line: 1, column: 10},
    });
  });
  it("reports EOF errors as a zero-width span at source length", () => {
    try {
      lowerPetrinetText("net", "truncated.petrinet");
    } catch (error) {
      expect(error).toBeInstanceOf(PetrinetDslError);
      expect((error as PetrinetDslError).diagnostic.span).toEqual({
        source: "truncated.petrinet",
        start: {offset: 3, line: 1, column: 4},
        end: {offset: 3, line: 1, column: 4},
      });
      return;
    }
    throw new Error("expected a Petri-net DSL diagnostic");
  });

  it("normalizes absolute source paths to portable diagnostic identities", () => {
    const compiled = compilePetrinetText("net portable\n", "/tmp/portable.petrinet");

    expect(compiled.documentKind).toBe("net");
    expect(compiled.document).toMatchObject({name: "portable"});
  });

  it("rejects unsafe integers before lossy JavaScript coercion", () => {
    for (const statement of [
      "[advance] priority 9007199254740992",
      "[advance] order 9007199254740992",
      "marking initial (queue) <- 9007199254740992",
    ]) {
      expectDslDiagnostic(
        () => lowerPetrinetText(
          `net unsafe\n\n(queue) -> [advance]\n${statement}\n`,
          "unsafe.petrinet",
        ),
        "PN101",
        "integer exceeds the safe IEEE-754 range",
      );
    }
  });

  it("uses Python binary64 spellings in exact numeric diagnostics", () => {
    for (const [lexeme, rendered] of [
      ["1e-6", "1e-06"],
      ["1e20", "1e+20"],
      ["-0.0", "-0.0"],
    ] as const) {
      expectDslDiagnostic(
        () => compilePetrinetText(
          `net numbers\n\n@weighted: (queue) -> [advance]\n@weighted weight ${lexeme}\n`,
          "numbers.petrinet",
        ),
        "PN202",
        `arc weight must be an integer greater than or equal to 1; got ${rendered} for @weighted`,
      );
    }
  });

  it("requires object token data when materializing templates", () => {
    expectDslDiagnostic(
      () => compilePetrinetText(
        'net templates\n\n(queue) -> [advance]\nmarking initial (queue) <- $payload\n$payload: token "scalar"\n',
        "templates.petrinet",
      ),
      "PN202",
      "template $payload data must be a JSON object",
    );
  });

  it("keeps handler facts idempotent and rejects conflicts or unknown targets", () => {
    const equal = compilePetrinetText(
      'net handlers\n\n[advance]\n[advance] handler "go"\n[advance] handler "go"\n',
      "handlers.petrinet",
    );
    expect(equal.document).toMatchObject({
      transitions: [{name: "advance", handler: "go"}],
    });

    expectDslDiagnostic(
      () => compilePetrinetText(
        'net handlers\n\n[advance]\n[advance] handler "go"\n[advance] handler "stop"\n',
        "handlers.petrinet",
      ),
      "PN202",
      "conflicting handler facts for transition [advance]",
    );
    expectDslDiagnostic(
      () => compilePetrinetText(
        'net handlers\n\n[ghost] handler "go"\n',
        "handlers.petrinet",
      ),
      "PN202",
      "handler refers to unknown transition [ghost]",
    );
  });

  it("counts UTF-8 bytes separately from scalar columns in relative spans", () => {
    const source =
      'net unicode\n\n(known)\nview "😀" position ("two words") at {"x": 0, "y": 0}\n';
    try {
      compilePetrinetText(source, "unicode.petrinet");
    } catch (error) {
      expect(error).toBeInstanceOf(PetrinetDslError);
      const diagnostic = (error as PetrinetDslError).diagnostic;
      const target = '("two words")';
      const targetStart = source.indexOf(target);
      expect(diagnostic.span.start).toEqual({
        offset: new TextEncoder().encode(source.slice(0, targetStart)).byteLength,
        line: 4,
        column: 19,
      });
      expect(diagnostic.span.end).toEqual({
        offset: new TextEncoder().encode(
          source.slice(0, targetStart + target.length),
        ).byteLength,
        line: 4,
        column: 32,
      });
      return;
    }
    throw new Error("expected a Petri-net DSL diagnostic");
  });

});

describe("closed Contribution IR wire gate", () => {
  const validIr = lowerPetrinetText("net stable\n", "stable.petrinet");

  it("runs as a standalone validator and rejects unknown closed fields", () => {
    expect(validateContributionIr(validIr)).toEqual({ok: true});

    const extraRoot = {...validIr, unexpected: true};
    const result = validateContributionIr(extraRoot);
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.errors.some((error) => error.keyword === "additionalProperties"))
        .toBe(true);
    }
  });

  it("rejects unsupported format and version before semantic resolution", () => {
    for (const mutation of [
      {format: "velocitron.petrinet/contribution-ir-next"},
      {version: 2},
    ]) {
      const malformed = Object.assign(cloneIr(validIr), mutation);
      expectDslDiagnostic(
        () => resolveContributionIr(malformed),
        "PN200",
        "unsupported Contribution IR format or version",
      );
    }
  });

  it("rejects root, contribution, and contribution-value unknown fields", () => {
    const extraRoot = cloneIr(validIr);
    extraRoot.extra = null;
    expectDslDiagnostic(
      () => resolveContributionIr(extraRoot),
      "PN200",
      "invalid Contribution IR document shape",
    );

    const extraContribution = cloneIr(validIr);
    const contributions = extraContribution.contributions as MutableObject[];
    (contributions[0] as MutableObject).extra = null;
    expectDslDiagnostic(
      () => resolveContributionIr(extraContribution),
      "PN200",
      "invalid Contribution IR contribution shape",
    );

    const extraValue = cloneIr(validIr);
    const header = (extraValue.contributions as MutableObject[])[0] as MutableObject;
    (header.value as MutableObject).extra = null;
    expectDslDiagnostic(
      () => resolveContributionIr(extraValue),
      "PN200",
      "invalid net header contribution",
    );
  });
});

describe("compiler resource budgets", () => {
  it("enforces the UTF-8 source-byte cap before lexing", () => {
    const error = captureResource(() =>
      lowerPetrinetText(
        "x".repeat(PETRINET_RESOURCE_LIMITS.sourceBytes + 1),
        "large.petrinet",
      ),
    );
    expect(error).toMatchObject({
      operation: "compile",
      resource: "sourceBytes",
      limit: PETRINET_RESOURCE_LIMITS.sourceBytes,
      actual: PETRINET_RESOURCE_LIMITS.sourceBytes + 1,
    });
  });

  it("enforces lexer-token and contribution caps", () => {
    const lexerError = captureResource(() =>
      lowerPetrinetText(`net n\n${"(p)\n".repeat(17_000)}`, "tokens.petrinet"),
    );
    expect(lexerError).toMatchObject({
      operation: "compile",
      resource: "lexerTokens",
      limit: PETRINET_RESOURCE_LIMITS.lexerTokens,
      actual: PETRINET_RESOURCE_LIMITS.lexerTokens + 1,
    });

    const contributionError = captureResource(() =>
      lowerPetrinetText(
        `net n\n${"(p)\n".repeat(PETRINET_RESOURCE_LIMITS.contributions)}`,
        "contributions.petrinet",
      ),
    );
    expect(contributionError).toMatchObject({
      operation: "compile",
      resource: "contributions",
      limit: PETRINET_RESOURCE_LIMITS.contributions,
      actual: PETRINET_RESOURCE_LIMITS.contributions + 1,
    });
  });

  it("caps marking expansion before allocating token objects", () => {
    const error = captureResource(() =>
      compilePetrinetText(
        "net n\n(p)\nmarking initial (p) <- 10001\n",
        "marking-limit.petrinet",
      ),
    );
    expect(error).toMatchObject({
      operation: "compile",
      resource: "materializedTokens",
      limit: PETRINET_RESOURCE_LIMITS.materializedTokens,
      actual: PETRINET_RESOURCE_LIMITS.materializedTokens + 1,
    });
  });

  it("enforces syntax-diagnostic and nesting caps", () => {
    const diagnosticError = captureResource(() =>
      lowerPetrinetText(`net n\n${"[t] handler\n".repeat(101)}`, "diagnostics.petrinet"),
    );
    expect(diagnosticError).toMatchObject({
      operation: "compile",
      resource: "diagnostics",
      limit: PETRINET_RESOURCE_LIMITS.diagnostics,
      actual: PETRINET_RESOURCE_LIMITS.diagnostics + 1,
    });

    const nestingError = captureResource(() =>
      lowerPetrinetText(
        `net n\nextensions ${"[".repeat(65)}null${"]".repeat(65)}\n`,
        "nested.petrinet",
      ),
    );
    expect(nestingError).toMatchObject({
      operation: "compile",
      resource: "nestingDepth",
      limit: PETRINET_RESOURCE_LIMITS.nestingDepth,
      actual: PETRINET_RESOURCE_LIMITS.nestingDepth + 1,
    });
  });

  it("enforces recursive IR-node and nesting budgets before validation", () => {
    const base = lowerPetrinetText("net n\n", "ir.petrinet");
    const tooManyNodes = {...base, extra: Array(10_001).fill(null)};
    const nodeError = captureResource(() => resolveContributionIr(tooManyNodes));
    expect(nodeError).toMatchObject({
      operation: "compile",
      resource: "irNodes",
      limit: PETRINET_RESOURCE_LIMITS.irNodes,
      actual: PETRINET_RESOURCE_LIMITS.irNodes + 1,
    });

    let nested: unknown = null;
    for (let depth = 0; depth <= PETRINET_RESOURCE_LIMITS.nestingDepth; depth += 1) {
      nested = [nested];
    }
    const depthError = captureResource(() =>
      resolveContributionIr({...base, extra: nested}),
    );
    expect(depthError).toMatchObject({
      operation: "compile",
      resource: "nestingDepth",
      limit: PETRINET_RESOURCE_LIMITS.nestingDepth,
    });
  });
});
