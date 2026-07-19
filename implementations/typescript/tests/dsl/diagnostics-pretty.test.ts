import {describe, expect, it} from "vitest";
import {
  PETRINET_RESOURCE_LIMITS,
  PetrinetDslError,
  PetrinetResourceError,
} from "../../src/dsl/diagnostics.js";
import {renderCanonicalJson} from "../../src/dsl/pretty.js";
import {SourceMap} from "../../src/dsl/source.js";
import type {JSONValue} from "../../src/dsl/types.js";

describe("DSL source coordinates and diagnostics", () => {
  it("maps UTF-16 indices to UTF-8 and Unicode-scalar CRLF positions", () => {
    const source = new SourceMap("scalar.petrinet", "a😀\r\n雪");

    expect(source.position(1)).toEqual({offset: 1, line: 1, column: 2});
    expect(source.position(2)).toEqual({offset: 1, line: 1, column: 2});
    expect(source.position(3)).toEqual({offset: 5, line: 1, column: 3});
    expect(source.position(4)).toEqual({offset: 6, line: 1, column: 4});
    expect(source.position(5)).toEqual({offset: 7, line: 2, column: 1});
    expect(source.eofSpan()).toEqual({
      source: "scalar.petrinet",
      start: {offset: 10, line: 2, column: 2},
      end: {offset: 10, line: 2, column: 2},
    });
  });

  it("renders the stable primary/help diagnostic and keeps resources distinct", () => {
    const span = new SourceMap("bad.petrinet", "bad").span(0, 3);
    const error = new PetrinetDslError({
      code: "PN101",
      message: "bad statement",
      span,
      help: "write a declaration",
      related: [{message: "first declaration", span}],
    });

    expect(error.message).toBe(
      "bad.petrinet:1:1: error[PN101]: bad statement\nhelp: write a declaration",
    );
    expect(error.diagnostic.related).toHaveLength(1);
    expect(Object.isFrozen(error.diagnostic)).toBe(true);

    const resource = new PetrinetResourceError("lexerTokens");
    expect(resource).not.toBeInstanceOf(PetrinetDslError);
    expect(resource.limit).toBe(PETRINET_RESOURCE_LIMITS.lexerTokens);
  });
});

describe("canonical pretty JSON", () => {
  it("uses RFC 8785 binary64 spellings and semantic array order", () => {
    const values: JSONValue = [
      -9_007_199_254_740_991,
      9_007_199_254_740_991,
      -0,
      1,
      0.000001,
      0.0000001,
      1e20,
      1e21,
      333333333.33333329,
      4.5,
      2e-3,
      1e-27,
      5e-324,
      1.7976931348623157e308,
      9007199254740992,
    ];

    expect(renderCanonicalJson(values)).toBe(
      "[\n" +
      "  -9007199254740991,\n" +
      "  9007199254740991,\n" +
      "  0,\n" +
      "  1,\n" +
      "  0.000001,\n" +
      "  1e-7,\n" +
      "  100000000000000000000,\n" +
      "  1e21,\n" +
      "  333333333.3333333,\n" +
      "  4.5,\n" +
      "  0.002,\n" +
      "  1e-27,\n" +
      "  5e-324,\n" +
      "  1.7976931348623157e308,\n" +
      "  9007199254740992\n" +
      "]\n",
    );
  });

  it("sorts object names explicitly by UTF-16 code units", () => {
    const value: JSONValue = {
      "\ue000": 2,
      "😀": [{"é": "雪", a: -0}, 1e21, 1e-7],
    };

    expect(renderCanonicalJson(value)).toBe(
      "{\n" +
      "  \"😀\": [\n" +
      "    {\n" +
      "      \"a\": 0,\n" +
      "      \"é\": \"雪\"\n" +
      "    },\n" +
      "    1e21,\n" +
      "    1e-7\n" +
      "  ],\n" +
      "  \"\": 2\n" +
      "}\n",
    );
  });

  it("rejects non-finite numbers, lone surrogates, and non-JSON values", () => {
    expect(() => renderCanonicalJson({outer: [Number.NaN]})).toThrow(/finite/u);
    expect(() => renderCanonicalJson("\ud800")).toThrow(/Unicode scalars/u);
    expect(() => renderCanonicalJson(1n as unknown as JSONValue)).toThrow(/not a JSON value/u);
  });
});
