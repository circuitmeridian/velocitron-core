// @ts-expect-error -- Node globals are provided by the Vitest process; the
// browser-safe production package intentionally does not depend on @types/node.
import {readFileSync} from "node:fs";
// @ts-expect-error -- See the node:fs import above.
import {fileURLToPath} from "node:url";
import {describe, expect, it} from "vitest";
import {
  compilePetrinetText,
  PetrinetDslError,
  type Diagnostic,
  type SourceSpan,
} from "../../src/dsl/api.js";
import {
  lowerPetrinetText,
  resolveContributionIr,
  validateContributionIr,
  type ContributionIr,
  type JSONObject,
} from "../../src/dsl/internal.js";

interface SourceFixture {
  readonly sourceId: string;
  readonly text: string;
}

interface ValidFixture {
  readonly canonicalComposition?: string;
  readonly canonicalNet?: string;
  readonly canonicalSource: string;
  readonly contributionIr: string;
  readonly expect: {
    readonly source?: string;
    readonly canonicalSourceFixedPoint: true;
  };
}

interface InvalidFixture {
  readonly expectation: string;
  readonly id: string;
  readonly phase: "parse" | "resolution";
  readonly structureParses: boolean;
}

interface ManifestCase {
  readonly id: string;
  readonly invalid?: readonly InvalidFixture[];
  readonly valid: ValidFixture;
}

interface Manifest {
  readonly cases: readonly ManifestCase[];
  readonly format: string;
  readonly version: number;
}

interface FixturePosition {
  readonly byteOffset: number;
  readonly column: number;
  readonly line: number;
}

interface FixtureSpan {
  readonly end: FixturePosition;
  readonly source: string;
  readonly start: FixturePosition;
}

interface FixtureDiagnostic {
  readonly code: string;
  readonly column: number;
  readonly file: string;
  readonly help?: string;
  readonly line: number;
  readonly message: string;
  readonly related?: readonly {
    readonly message: string;
    readonly span: FixtureSpan;
  }[];
  readonly span: FixtureSpan;
}

interface SourceInvalidExpectation {
  readonly diagnostic: FixtureDiagnostic;
  readonly source: SourceFixture;
}

interface NetInvalidExpectation {
  readonly diagnostic: FixtureDiagnostic;
  readonly net: JSONObject;
}

const corpusDirectory = fileURLToPath(
  new URL("../../../../spec/conformance/petrinet/", import.meta.url),
);

function readText(relativePath: string): string {
  return readFileSync(`${corpusDirectory}/${relativePath}`, "utf8");
}

function readJson<T>(relativePath: string): T {
  return JSON.parse(readText(relativePath)) as T;
}

const manifest = readJson<Manifest>("manifest.json");
const validCases = manifest.cases.map((entry) => ({
  id: entry.id,
  fixture: entry.valid,
}));
const invalidCases = manifest.cases.flatMap((entry) =>
  (entry.invalid ?? []).map((fixture) => ({
    caseId: entry.id,
    fixture,
    id: `${entry.id}/${fixture.id}`,
  })),
);

const parseCaseIds = [
  "10-wired_pulse/missing-use-alias",
  "11-speaks-window/non-object-extensions",
  "11-speaks-window/unsupported-route-style",
  "11-speaks-window/underscore-non-object-extensions",
  "12-guinan-graduation/unsupported-pipeline",
] as const;

const resolvedNetCaseIds = [
  "11-speaks-window/arc-handle-out-of-range",
  "11-speaks-window/arc-handle-stale-from",
  "11-speaks-window/arc-handle-stale-mode",
  "11-speaks-window/arc-handle-stale-to",
  "11-speaks-window/arc-handle-stale-type",
  "11-speaks-window/arc-handle-unknown-field",
  "11-speaks-window/extensions-non-object",
  "11-speaks-window/fingerprint-unknown-field",
  "11-speaks-window/marking-unknown-field",
  "11-speaks-window/named-marking-unknown-target",
  "11-speaks-window/route-point-missing-y",
  "11-speaks-window/route-unknown-field",
  "11-speaks-window/route-unknown-handle",
  "11-speaks-window/v1-unknown-field",
  "11-speaks-window/view-point-missing-y",
  "11-speaks-window/view-point-unknown-field",
  "11-speaks-window/view-position-invalid-target-kind",
  "11-speaks-window/view-position-unknown-target",
  "11-speaks-window/view-route-empty-points",
  "11-speaks-window/view-route-invalid-style",
  "11-speaks-window/view-unknown-field",
] as const;

const compositionLoaderCaseIds = [
  "10-wired_pulse/unknown-wire-port",
  "10-wired_pulse/reversed-wire-direction",
  "10-wired_pulse/wire-type-mismatch",
] as const;

function validSource(fixture: ValidFixture): SourceFixture {
  return readJson<SourceFixture>(fixture.expect.source ?? fixture.canonicalSource);
}


function canonicalDocumentPath(fixture: ValidFixture): string {
  const path = fixture.canonicalNet ?? fixture.canonicalComposition;
  if (path === undefined) throw new Error("valid fixture lacks a canonical document");
  return path;
}

function serializeSpan(span: SourceSpan): FixtureSpan {
  return {
    end: {
      byteOffset: span.end.offset,
      column: span.end.column,
      line: span.end.line,
    },
    source: span.source,
    start: {
      byteOffset: span.start.offset,
      column: span.start.column,
      line: span.start.line,
    },
  };
}

function serializeDiagnostic(diagnostic: Diagnostic): FixtureDiagnostic {
  return {
    code: diagnostic.code,
    column: diagnostic.span.start.column,
    file: diagnostic.span.source,
    ...(diagnostic.help === undefined ? {} : {help: diagnostic.help}),
    line: diagnostic.span.start.line,
    message: diagnostic.message,
    ...(diagnostic.related === undefined
      ? {}
      : {
          related: diagnostic.related.map((item) => ({
            message: item.message,
            span: serializeSpan(item.span),
          })),
        }),
    span: serializeSpan(diagnostic.span),
  };
}

function captureDiagnostic(source: SourceFixture): FixtureDiagnostic {
  try {
    compilePetrinetText(source.text, source.sourceId);
  } catch (error) {
    expect(error).toBeInstanceOf(PetrinetDslError);
    return serializeDiagnostic((error as PetrinetDslError).diagnostic);
  }
  throw new Error(`expected ${source.sourceId} to produce a DSL diagnostic`);
}

const sourceInvalidCases = invalidCases.filter(({fixture}) => {
  const expectation = readJson<SourceInvalidExpectation | NetInvalidExpectation>(
    fixture.expectation,
  );
  return "source" in expectation;
});
const resolvedNetCases = invalidCases.filter(({fixture}) => {
  const expectation = readJson<SourceInvalidExpectation | NetInvalidExpectation>(
    fixture.expectation,
  );
  return "net" in expectation;
});
const compositionLoaderCases = sourceInvalidCases.filter(({id}) =>
  (compositionLoaderCaseIds as readonly string[]).includes(id),
);
const compilerDiagnosticCases = sourceInvalidCases.filter(({id}) =>
  !(compositionLoaderCaseIds as readonly string[]).includes(id),
);

describe("SIM-06 conformance manifest coverage", () => {
  it("pins the manifest identity and exhaustive case counts", () => {
    expect(manifest).toMatchObject({
      format: "velocitron.petrinet/conformance-corpus",
      version: 1,
    });
    expect(validCases).toHaveLength(26);
    expect(invalidCases).toHaveLength(70);
    expect(sourceInvalidCases).toHaveLength(49);
    expect(resolvedNetCases).toHaveLength(21);
    expect(compilerDiagnosticCases).toHaveLength(46);
    expect(compositionLoaderCases).toHaveLength(3);
  });

  it("enumerates all parse-phase cases explicitly", () => {
    expect(
      invalidCases
        .filter(({fixture}) => fixture.phase === "parse")
        .map(({id}) => id),
    ).toEqual(parseCaseIds);
  });

  it("enumerates all resolved-net fixtures as downstream unsupported", () => {
    expect(resolvedNetCases.map(({id}) => id)).toEqual(resolvedNetCaseIds);
  });

  it("enumerates composition-loader diagnostics as downstream unsupported", () => {
    expect(compositionLoaderCases.map(({id}) => id)).toEqual(
      compositionLoaderCaseIds,
    );
  });
});

describe("valid corpus lowering", () => {
  it.each(validCases)("$id lowers to the complete Contribution IR", ({fixture}) => {
    const source = validSource(fixture);
    const expected = readJson<ContributionIr>(fixture.contributionIr);

    const actual = lowerPetrinetText(source.text, source.sourceId);
    expect(actual).toEqual(expected);
    expect(validateContributionIr(actual)).toEqual({ok: true});
  });
});

describe("valid corpus resolution", () => {
  it.each(validCases)("$id resolves fixture IR to canonical JSON", ({fixture}) => {
    const contributionIr = readJson<ContributionIr>(fixture.contributionIr);
    const documentPath = canonicalDocumentPath(fixture);
    const expected = readJson<JSONObject>(documentPath);

    expect(resolveContributionIr(contributionIr)).toEqual(expected);
  });
});


describe("valid corpus public compilation", () => {
  it.each(validCases)("$id compiles with its document kind and full document", ({fixture}) => {
    const source = validSource(fixture);
    const documentPath = canonicalDocumentPath(fixture);
    const expected = readJson<JSONObject>(documentPath);
    const compiled = compilePetrinetText(source.text, source.sourceId);

    expect(compiled.documentKind).toBe(
      fixture.canonicalNet === undefined ? "composition" : "net",
    );
    expect(compiled.document).toEqual(expected);
  });
});

describe("canonical corpus source", () => {
  it.each(validCases)("$id compiles the committed canonical source", ({fixture}) => {
    expect(fixture.expect.canonicalSourceFixedPoint).toBe(true);
    const source = readJson<SourceFixture>(fixture.canonicalSource);
    const expected = readJson<JSONObject>(canonicalDocumentPath(fixture));
    const compiled = compilePetrinetText(source.text, source.sourceId);

    expect(compiled.documentKind).toBe(
      fixture.canonicalNet === undefined ? "composition" : "net",
    );
    expect(compiled.document).toEqual(expected);
  });
});

describe("invalid corpus diagnostics owned by the compiler", () => {
  it.each(compilerDiagnosticCases)(
    "$id serializes the exact $fixture.phase diagnostic",
    ({fixture}) => {
      const expectation = readJson<SourceInvalidExpectation>(fixture.expectation);

      expect(captureDiagnostic(expectation.source)).toEqual(expectation.diagnostic);
      expect(fixture.structureParses).toBe(fixture.phase !== "parse");
    },
  );
});

describe("invalid corpus cases owned by downstream stages", () => {
  it.each(compositionLoaderCases)(
    "$id compiles structurally and remains a composition-loader diagnostic",
    ({fixture}) => {
      const expectation = readJson<SourceInvalidExpectation>(fixture.expectation);
      const compiled = compilePetrinetText(
        expectation.source.text,
        expectation.source.sourceId,
      );

      expect(fixture.phase).toBe("resolution");
      expect(fixture.structureParses).toBe(true);
      expect(compiled.documentKind).toBe("composition");
    },
  );

  it.each(resolvedNetCases)(
    "$id is represented only by a resolved-net fixture",
    ({fixture}) => {
      const expectation = readJson<NetInvalidExpectation>(fixture.expectation);

      expect(fixture.phase).toBe("resolution");
      expect(fixture.structureParses).toBe(true);
      expect(expectation).toHaveProperty("net");
      expect(expectation).not.toHaveProperty("source");
    },
  );
});
