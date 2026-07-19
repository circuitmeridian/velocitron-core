import {createDefaultCelAdapter} from "../cel/default.js";
import {
  PETRINET_RESOURCE_LIMITS,
  PetrinetDslError,
  PetrinetResourceError,
  type Diagnostic,
  type RelatedDiagnostic,
  type SourceSpan,
} from "./diagnostics.js";
import {validateContributionIr} from "./ir/generated-validator.js";
import type {ContributionIr, JSONObject, JSONValue} from "./types.js";

type AnyObject = Record<string, any>;
type IrPosition = {byteOffset: number; line: number; column: number};
type IrSpan = {source: string; start: IrPosition; end: IrPosition};
type Identity = readonly [source: string, statement: number, part: number];
type Fact<T> = {value: T; span: IrSpan};
type ArcDeclaration = {
  id: Identity;
  place: string;
  transition: string;
  isInput: boolean;
  mode: "consume" | "read" | "inhibit" | "produce";
  color: {kind: "explicit"; value: string};
  span: IrSpan;
  transitionNameSpan: IrSpan;
};
type ResolvedArc = AnyObject;

const RELATIVE_SOURCE_ID = /^(?!\/)(?![A-Za-z]:[\\/])(?![A-Za-z][A-Za-z0-9+.-]*:).+$/u;
const TIMER_BIND_IDENTIFIER = /^[A-Za-z_][A-Za-z0-9_]*$/u;
const JSON_NUMBER = /^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?$/u;

function isObject(value: unknown): value is AnyObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function keysEqual(value: AnyObject, expected: readonly string[]): boolean {
  const keys = Object.keys(value);
  return keys.length === expected.length && expected.every((key) => Object.hasOwn(value, key));
}

function setOwn(object: AnyObject, key: string, value: unknown): void {
  Object.defineProperty(object, key, {
    configurable: true,
    enumerable: true,
    value,
    writable: true,
  });
}

function containsIsolatedSurrogate(value: string): boolean {
  for (let index = 0; index < value.length; index += 1) {
    const unit = value.charCodeAt(index);
    if (unit >= 0xd800 && unit <= 0xdbff) {
      const next = value.charCodeAt(index + 1);
      if (next >= 0xdc00 && next <= 0xdfff) {
        index += 1;
        continue;
      }
      return true;
    }
    if (unit >= 0xdc00 && unit <= 0xdfff) return true;
  }
  return false;
}

function unicodeScalarLength(value: string): number {
  let length = 0;
  for (const _character of value) length += 1;
  return length;
}

const PYTHON_CHARACTER_ESCAPES: Readonly<Record<string, string>> = {
  "\u0007": "\\a",
  "\b": "\\b",
  "\t": "\\t",
  "\n": "\\n",
  "\u000b": "\\v",
  "\f": "\\f",
  "\r": "\\r",
};
const PYTHON_NON_PRINTABLE = /[\p{C}\p{Z}]/u;

function pythonRepr(value: unknown): string {
  if (value === null) return "None";
  if (value === true) return "True";
  if (value === false) return "False";
  if (typeof value === "string") {
    const quote = value.includes("'") && !value.includes('"') ? '"' : "'";
    let rendered = quote;
    for (const character of value) {
      const escaped = PYTHON_CHARACTER_ESCAPES[character];
      if (escaped !== undefined) {
        rendered += escaped;
        continue;
      }
      if (character === "\\" || character === quote) {
        rendered += `\\${character}`;
        continue;
      }
      if (character !== " " && PYTHON_NON_PRINTABLE.test(character)) {
        const codePoint = character.codePointAt(0) as number;
        rendered += codePoint <= 0xff
          ? `\\x${codePoint.toString(16).padStart(2, "0")}`
          : codePoint <= 0xffff
            ? `\\u${codePoint.toString(16).padStart(4, "0")}`
            : `\\U${codePoint.toString(16).padStart(8, "0")}`;
        continue;
      }
      rendered += character;
    }
    return `${rendered}${quote}`;
  }
  if (Array.isArray(value)) return `[${value.map(pythonRepr).join(", ")}]`;
  if (isObject(value)) {
    return `{${Object.entries(value)
      .map(([key, item]) => `${pythonRepr(key)}: ${pythonRepr(item)}`)
      .join(", ")}}`;
  }
  return String(value);
}

function toDiagnosticSpan(span?: AnyObject | null): SourceSpan {
  if (
    span !== null &&
    span !== undefined &&
    isObject(span.start) &&
    isObject(span.end)
  ) {
    return {
      source: String(span.source),
      start: {
        offset: span.start.byteOffset as number,
        line: span.start.line as number,
        column: span.start.column as number,
      },
      end: {
        offset: span.end.byteOffset as number,
        line: span.end.line as number,
        column: span.end.column as number,
      },
    };
  }
  const position = {offset: 0, line: 1, column: 1};
  return {source: "<ir>", start: position, end: position};
}

function resolutionError(
  code: string,
  message: string,
  span?: AnyObject | null,
  help?: string,
  related: readonly (readonly [message: string, span: IrSpan])[] = [],
): PetrinetDslError {
  const relatedDiagnostics: readonly RelatedDiagnostic[] | undefined =
    related.length === 0
      ? undefined
      : related.map(([relatedMessage, relatedSpan]) => ({
          message: relatedMessage,
          span: toDiagnosticSpan(relatedSpan),
        }));
  const diagnostic: Diagnostic = {
    code,
    message,
    span: toDiagnosticSpan(span),
    ...(help === undefined ? {} : {help}),
    ...(relatedDiagnostics === undefined ? {} : {related: relatedDiagnostics}),
  };
  return new PetrinetDslError(diagnostic);
}

function preflightContributionIr(value: unknown): void {
  if (isObject(value) && Array.isArray(value.contributions)) {
    const count = value.contributions.length;
    if (count > PETRINET_RESOURCE_LIMITS.contributions) {
      throw new PetrinetResourceError(
        "contributions",
        PETRINET_RESOURCE_LIMITS.contributions,
        count,
      );
    }
  }

  let nodes = 0;
  const active = new WeakSet<object>();
  const visit = (item: unknown, depth: number): void => {
    if (depth > PETRINET_RESOURCE_LIMITS.nestingDepth) {
      throw new PetrinetResourceError(
        "nestingDepth",
        PETRINET_RESOURCE_LIMITS.nestingDepth,
        depth,
      );
    }
    nodes += 1;
    if (nodes > PETRINET_RESOURCE_LIMITS.irNodes) {
      throw new PetrinetResourceError(
        "irNodes",
        PETRINET_RESOURCE_LIMITS.irNodes,
        nodes,
      );
    }
    if (typeof item !== "object" || item === null) return;
    if (active.has(item)) {
      throw resolutionError("PN200", "invalid Contribution IR document shape");
    }
    active.add(item);
    if (Array.isArray(item)) {
      for (const child of item) visit(child, depth + 1);
    } else {
      for (const child of Object.values(item)) visit(child, depth + 1);
    }
    active.delete(item);
  };
  visit(value, 0);
}

function relativeSpan(span: IrSpan, prefix: string, target: string): IrSpan {
  const prefixScalars = unicodeScalarLength(prefix);
  const targetScalars = unicodeScalarLength(target);
  const prefixBytes = new TextEncoder().encode(prefix).byteLength;
  const targetBytes = new TextEncoder().encode(target).byteLength;
  const line = span.start.line;
  const column = span.start.column + prefixScalars;
  const byteOffset = span.start.byteOffset + prefixBytes;
  return {
    source: span.source,
    start: {byteOffset, line, column},
    end: {
      byteOffset: byteOffset + targetBytes,
      line,
      column: column + targetScalars,
    },
  };
}

function isSourceSpan(value: unknown, expectedSource: string): value is IrSpan {
  if (!isObject(value) || !keysEqual(value, ["source", "start", "end"])) return false;
  if (value.source !== expectedSource || !RELATIVE_SOURCE_ID.test(expectedSource)) return false;
  const positions: IrPosition[] = [];
  for (const rawPosition of [value.start, value.end]) {
    if (!isObject(rawPosition) || !keysEqual(rawPosition, ["byteOffset", "line", "column"])) {
      return false;
    }
    const {byteOffset, line, column} = rawPosition;
    if (
      !Number.isInteger(byteOffset) ||
      byteOffset < 0 ||
      !Number.isInteger(line) ||
      line < 1 ||
      !Number.isInteger(column) ||
      column < 1
    ) {
      return false;
    }
    positions.push({byteOffset, line, column});
  }
  const [start, end] = positions as [IrPosition, IrPosition];
  return (
    start.byteOffset <= end.byteOffset &&
    (start.line < end.line || (start.line === end.line && start.column <= end.column))
  );
}

function sourceIdentity(value: unknown, sourceMember: "source" | "document"): Identity | null {
  if (!isObject(value) || !keysEqual(value, [sourceMember, "statement", "part"])) return null;
  const source = value[sourceMember];
  const statement = value.statement;
  const part = value.part;
  if (
    typeof source !== "string" ||
    !RELATIVE_SOURCE_ID.test(source) ||
    !Number.isInteger(statement) ||
    statement < 0 ||
    !Number.isInteger(part) ||
    part < 0
  ) {
    return null;
  }
  return [source, statement, part];
}

function contributionIdentity(value: unknown): Identity | null {
  return sourceIdentity(value, "source");
}

function arcIdentity(value: unknown): Identity | null {
  return sourceIdentity(value, "document");
}

function identityKey(identity: Identity): string {
  return JSON.stringify(identity);
}

function sameIdentity(left: Identity, right: Identity): boolean {
  return left[0] === right[0] && left[1] === right[1] && left[2] === right[2];
}

function cloneJson<T>(value: T): T {
  if (Array.isArray(value)) return value.map((item) => cloneJson(item)) as T;
  if (isObject(value)) {
    const result: AnyObject = {};
    for (const [key, item] of Object.entries(value)) {
      setOwn(result, key, cloneJson(item));
    }
    return result as T;
  }
  return value;
}

function untag(value: AnyObject, depth = 0): JSONValue {
  if (depth > PETRINET_RESOURCE_LIMITS.nestingDepth) {
    throw new PetrinetResourceError(
      "nestingDepth",
      PETRINET_RESOURCE_LIMITS.nestingDepth,
      depth,
    );
  }
  const kind = value.type;
  if (kind === "null") {
    if (!keysEqual(value, ["type"])) throw new Error("invalid tagged JSON");
    return null;
  }
  if (kind === "boolean") {
    if (!keysEqual(value, ["type", "value"]) || typeof value.value !== "boolean") {
      throw new Error("invalid tagged JSON");
    }
    return value.value;
  }
  if (kind === "string") {
    if (
      !keysEqual(value, ["type", "value"]) ||
      typeof value.value !== "string" ||
      containsIsolatedSurrogate(value.value)
    ) {
      throw new Error("invalid tagged JSON");
    }
    return value.value;
  }
  if (kind === "number") {
    const lexeme = value.lexeme;
    if (
      !keysEqual(value, ["type", "lexeme"]) ||
      typeof lexeme !== "string" ||
      !JSON_NUMBER.test(lexeme)
    ) {
      throw new Error("invalid tagged JSON");
    }
    const parsed = Number(lexeme);
    if (!Number.isFinite(parsed)) throw new Error("invalid tagged JSON");
    if (!/[.eE]/u.test(lexeme) && (BigInt(lexeme) > 9007199254740991n || BigInt(lexeme) < -9007199254740991n)) {
      throw new Error("invalid tagged JSON");
    }
    return Object.is(parsed, -0) ? 0 : parsed;
  }
  if (kind === "array") {
    if (!keysEqual(value, ["type", "items"]) || !Array.isArray(value.items)) {
      throw new Error("invalid tagged JSON");
    }
    return value.items.map((item: unknown) => {
      if (!isObject(item)) throw new Error("invalid tagged JSON");
      return untag(item, depth + 1);
    });
  }
  if (kind === "object") {
    if (!keysEqual(value, ["type", "entries"]) || !Array.isArray(value.entries)) {
      throw new Error("invalid tagged JSON");
    }
    const result: AnyObject = {};
    for (const rawEntry of value.entries as unknown[]) {
      if (!isObject(rawEntry) || !keysEqual(rawEntry, ["key", "value"])) {
        throw new Error("invalid tagged JSON");
      }
      const key = rawEntry.key;
      if (
        typeof key !== "string" ||
        Object.hasOwn(result, key) ||
        !isObject(rawEntry.value)
      ) {
        throw new Error("invalid tagged JSON");
      }
      setOwn(result, key, untag(rawEntry.value, depth + 1));
    }
    return result;
  }
  throw new Error("invalid tagged JSON");
}

function jsonValuesEqual(left: unknown, right: unknown): boolean {
  if (typeof left === "number" || typeof right === "number") {
    return typeof left === "number" && typeof right === "number" && left === right;
  }
  if (left === null || right === null || typeof left !== typeof right) return left === right;
  if (Array.isArray(left) || Array.isArray(right)) {
    return (
      Array.isArray(left) &&
      Array.isArray(right) &&
      left.length === right.length &&
      left.every((item, index) => jsonValuesEqual(item, right[index]))
    );
  }
  if (isObject(left) || isObject(right)) {
    if (!isObject(left) || !isObject(right)) return false;
    const leftKeys = Object.keys(left);
    const rightKeys = Object.keys(right);
    return (
      leftKeys.length === rightKeys.length &&
      leftKeys.every((key) => Object.hasOwn(right, key) && jsonValuesEqual(left[key], right[key]))
    );
  }
  return left === right;
}

function compactSortedJson(value: unknown): string {
  const normalize = (item: unknown): unknown => {
    if (Array.isArray(item)) return item.map(normalize);
    if (isObject(item)) {
      const result: AnyObject = {};
      for (const key of Object.keys(item).sort()) {
        setOwn(result, key, normalize(item[key]));
      }
      return result;
    }
    return item;
  };
  return JSON.stringify(normalize(value));
}

function validCapacityKey(value: unknown): value is string | string[] {
  return (
    (typeof value === "string" && value.length > 0) ||
    (Array.isArray(value) &&
      value.length > 0 &&
      value.every((item) => typeof item === "string" && item.length > 0))
  );
}

function parseMarkingToken(rawToken: unknown, message: string, span: IrSpan): AnyObject {
  if (!isObject(rawToken)) throw resolutionError("PN200", message, span);
  if (keysEqual(rawToken, ["template"])) {
    const rawTemplate = rawToken.template;
    if (
      !isObject(rawTemplate) ||
      !keysEqual(rawTemplate, ["type", "name"]) ||
      rawTemplate.type !== "template" ||
      typeof rawTemplate.name !== "string" ||
      rawTemplate.name.length === 0
    ) {
      throw resolutionError("PN200", message, span);
    }
    return {template: rawTemplate.name};
  }
  if (!keysEqual(rawToken, ["color", "data"])) throw resolutionError("PN200", message, span);
  if (
    typeof rawToken.color !== "string" ||
    rawToken.color.length === 0 ||
    !isObject(rawToken.data)
  ) {
    throw resolutionError("PN200", message, span);
  }
  let data: JSONValue;
  try {
    data = untag(rawToken.data);
  } catch (error) {
    if (error instanceof PetrinetResourceError) throw error;
    throw resolutionError("PN200", message, span);
  }
  if (!isObject(data)) throw resolutionError("PN200", message, span);
  return {color: rawToken.color, data};
}

function materializeMarkingToken(options: {
  place: string;
  token: AnyObject;
  count: number;
  acceptedColors: readonly string[];
  templates: ReadonlyMap<string, JSONValue>;
  span: IrSpan;
  markingName?: string;
}): AnyObject[] {
  const {place, token, count, acceptedColors, templates, span, markingName} = options;
  let color: string;
  let data: JSONValue;
  if (typeof token.template === "string") {
    if (!templates.has(token.template)) {
      throw resolutionError("PN202", `undefined template $${token.template}`, span);
    }
    if (acceptedColors.length !== 1) {
      throw resolutionError(
        "PN202",
        `template $${token.template} color is ambiguous at (${place})`,
        span,
      );
    }
    color = acceptedColors[0] as string;
    data = templates.get(token.template) as JSONValue;
    if (!isObject(data)) {
      throw resolutionError(
        "PN202",
        `template $${token.template} data must be a JSON object`,
        span,
      );
    }
  } else {
    color = token.color as string;
    if (!acceptedColors.includes(color)) {
      const subject = markingName === undefined ? "marking" : `named marking ${pythonRepr(markingName)}`;
      throw resolutionError(
        "PN202",
        `${subject} token color ${pythonRepr(color)} is not accepted by place (${place})`,
        span,
      );
    }
    data = token.data as JSONValue;
  }
  const result: AnyObject[] = [];
  for (let index = 0; index < count; index += 1) {
    result.push({type: color, data: cloneJson(data)});
  }
  return result;
}

function invalidContributionMessage(contribution: AnyObject): string {
  switch (contribution.kind) {
    case "document.net-header": return "invalid net header contribution";
    case "document.composition-header": return "invalid composition header contribution";
    case "place.declare": return "invalid place declaration contribution";
    case "transition.declare": return "invalid transition declaration contribution";
    case "place.accepts": return "invalid place accepted-colors contribution";
    case "place.port": return "invalid place port contribution";
    case "arc.handle": return "invalid arc handle contribution";
    case "order.transition": return "invalid transition order contribution";
    case "order.arc-run": return "invalid arc-run order contribution";
    case "place.capacity-per-color-key": return "invalid place capacity contribution";
    case "arc.weight": return "invalid arc weight contribution";
    case "arc.produce-data": return "invalid arc produce-data contribution";
    case "arc.produce-cel": return "invalid arc produce-cel contribution";
    case "arc.predicate": return "invalid arc predicate contribution";
    case "arc.correlate": return "invalid arc correlate contribution";
    case "arc.declare": return "invalid arc declaration";
    case "transition.timer": return "invalid transition timer contribution";
    case "transition.timer-maturity": return "invalid transition timer maturity contribution";
    case "transition.timer-bind": return "invalid transition timer bind contribution";
    case "transition.priority": return "invalid transition priority contribution";
    case "transition.handler": return "invalid transition handler contribution";
    case "transition.guard": return "invalid transition guard contribution";
    case "marking.append": return "invalid initial marking contribution";
    case "template.define": return "invalid template definition contribution";
    case "documentation.description": return "invalid documentation description contribution";
    case "documentation.annotation": return "invalid documentation annotation contribution";
    case "metadata.named-marking": return "invalid named marking contribution";
    case "view.position": return "invalid view position contribution";
    case "view.route": return "invalid view route contribution";
    case "document.extensions": return "invalid document extensions contribution";
    case "composition.use": return "invalid composition use";
    case "composition.wire": return "invalid composition wire";
    default: return `unsupported contribution kind ${pythonRepr(contribution.kind)}`;
  }
}

function onlyLegacyDocumentMetadataFailedSchema(
  documentIr: AnyObject,
  errors: readonly {instancePath: string}[],
): boolean {
  if (!Array.isArray(documentIr.contributions)) return false;
  const invalidIndexes = new Set<number>();
  for (const error of errors) {
    const match = /^\/contributions\/(\d+)(?:\/|$)/u.exec(error.instancePath);
    if (match === null) return false;
    invalidIndexes.add(Number(match[1]));
  }
  if (invalidIndexes.size === 0) return false;
  for (const index of invalidIndexes) {
    const contribution = documentIr.contributions[index];
    if (
      !isObject(contribution) ||
      (contribution.kind !== "documentation.description" &&
        contribution.kind !== "documentation.annotation") ||
      !isObject(contribution.target) ||
      !keysEqual(contribution.target, ["type"]) ||
      contribution.target.type !== "document"
    ) {
      return false;
    }
  }
  return true;
}

function rejectSchemaInvalidIr(documentIr: AnyObject, errors: readonly {instancePath: string}[]): never {
  if (documentIr.format !== "velocitron.petrinet/contribution-ir" || documentIr.version !== 1) {
    throw resolutionError("PN200", "unsupported Contribution IR format or version");
  }
  if (!keysEqual(documentIr, ["format", "version", "documentKind", "document", "contributions"])) {
    throw resolutionError("PN200", "invalid Contribution IR document shape");
  }
  if (!isObject(documentIr.document)) {
    throw resolutionError("PN200", "invalid Contribution IR document");
  }
  if (!Array.isArray(documentIr.contributions)) {
    throw resolutionError("PN200", "contributions must be an array");
  }
  let invalidIndex = Number.POSITIVE_INFINITY;
  for (const error of errors) {
    const match = /^\/contributions\/(\d+)(?:\/|$)/u.exec(error.instancePath);
    if (match !== null) invalidIndex = Math.min(invalidIndex, Number(match[1]));
  }
  if (!Number.isFinite(invalidIndex)) {
    throw resolutionError("PN200", "invalid Contribution IR document shape");
  }
  const contribution = documentIr.contributions[invalidIndex];
  if (!isObject(contribution)) {
    throw resolutionError("PN200", "invalid Contribution IR contribution shape");
  }
  const span = isObject(contribution.span) ? contribution.span : undefined;
  if (
    contribution.kind === "arc.declare" &&
    isObject(contribution.value) &&
    contribution.value.mode === "consume"
  ) {
    const identity = contributionIdentity(contribution.id);
    if (
      identity !== null &&
      !isSourceSpan(contribution.value.transitionNameSpan, identity[0])
    ) {
      throw resolutionError(
        "PN200",
        "consumed arc has invalid transitionNameSpan",
        span,
      );
    }
  }
  if (contribution.kind === "arc.weight" && isObject(contribution.value)) {
    const weight = contribution.value.weight;
    if (Number.isInteger(weight) && weight < 1 && isObject(contribution.target)) {
      throw resolutionError(
        "PN202",
        `arc weight must be an integer greater than or equal to 1; got ${pythonRepr(weight)} for @${String(contribution.target.name)}`,
        span,
      );
    }
  }
  if (contribution.kind === "place.capacity-per-color-key" && isObject(contribution.value) && isObject(contribution.target)) {
    const name = String(contribution.target.name);
    if (!keysEqual(contribution.value, ["key", "max"])) {
      throw resolutionError(
        "PN202",
        `capacityPerColorKey for (${name}) must contain exactly key and max`,
        span,
      );
    }
    if (!validCapacityKey(contribution.value.key)) {
      throw resolutionError(
        "PN202",
        `capacityPerColorKey key must be a non-empty string or non-empty array of non-empty strings for (${name})`,
        span,
      );
    }
    const maximum = contribution.value.max;
    if (!Number.isInteger(maximum) || maximum < 1) {
      throw resolutionError(
        "PN202",
        `capacityPerColorKey max must be an integer greater than or equal to 1; got ${pythonRepr(maximum)} for (${name})`,
        span,
      );
    }
  }
  if (
    contribution.kind === "view.route" &&
    isObject(contribution.target) &&
    typeof contribution.target.name === "string" &&
    isObject(contribution.value) &&
    isObject(contribution.value.view) &&
    typeof contribution.value.view.name === "string" &&
    Array.isArray(contribution.value.points) &&
    contribution.value.points.length === 0
  ) {
    throw resolutionError(
      "PN202",
      `view ${pythonRepr(contribution.value.view.name)} route @${contribution.target.name} requires at least one point`,
      span,
      'add at least one {"x": Number, "y": Number} point',
    );
  }
  throw resolutionError("PN200", invalidContributionMessage(contribution), span);
}

function validateEnvelope(ir: unknown): {
  documentIr: AnyObject;
  documentId: string;
  documentKind: "net" | "composition";
  contributions: AnyObject[];
} {
  preflightContributionIr(ir);
  const schemaResult = validateContributionIr(ir);
  if (!isObject(ir)) throw resolutionError("PN200", "invalid Contribution IR document shape");
  const documentIr = ir;
  if (!keysEqual(documentIr, ["format", "version", "documentKind", "document", "contributions"])) {
    if (!schemaResult.ok) rejectSchemaInvalidIr(documentIr, schemaResult.errors);
    throw resolutionError("PN200", "invalid Contribution IR document shape");
  }
  if (documentIr.format !== "velocitron.petrinet/contribution-ir" || documentIr.version !== 1) {
    throw resolutionError("PN200", "unsupported Contribution IR format or version");
  }
  const rawDocument = documentIr.document;
  if (!isObject(rawDocument)) throw resolutionError("PN200", "invalid Contribution IR document");
  const documentId = rawDocument.id;
  const documentKind = documentIr.documentKind;
  if (
    (documentKind !== "net" && documentKind !== "composition") ||
    !keysEqual(rawDocument, ["id"]) ||
    typeof documentId !== "string" ||
    !RELATIVE_SOURCE_ID.test(documentId)
  ) {
    throw resolutionError("PN200", "invalid Contribution IR document");
  }
  if (!Array.isArray(documentIr.contributions)) {
    throw resolutionError("PN200", "contributions must be an array");
  }
  const contributions = documentIr.contributions as unknown[];
  const ids = new Set<string>();
  for (let ordinal = 0; ordinal < contributions.length; ordinal += 1) {
    const rawContribution = contributions[ordinal];
    if (!isObject(rawContribution) || !keysEqual(rawContribution, ["id", "kind", "ordinal", "span", "target", "value"])) {
      throw resolutionError("PN200", "invalid Contribution IR contribution shape");
    }
    if (!Number.isInteger(rawContribution.ordinal) || rawContribution.ordinal !== ordinal) {
      throw resolutionError(
        "PN200",
        "contribution ordinals must be contiguous zero-based integers",
      );
    }
    const uniqueId = contributionIdentity(rawContribution.id);
    if (uniqueId === null) throw resolutionError("PN200", "invalid contribution identity");
    if (uniqueId[0] !== documentId) {
      throw resolutionError("PN200", "contribution identity source must match document id");
    }
    if (!isSourceSpan(rawContribution.span, uniqueId[0])) {
      throw resolutionError("PN200", "invalid contribution span");
    }
    const key = identityKey(uniqueId);
    if (ids.has(key)) throw resolutionError("PN200", "duplicate contribution identity");
    ids.add(key);
  }
  if (
    !schemaResult.ok &&
    !onlyLegacyDocumentMetadataFailedSchema(documentIr, schemaResult.errors)
  ) {
    rejectSchemaInvalidIr(documentIr, schemaResult.errors);
  }
  return {
    documentIr,
    documentId,
    documentKind,
    contributions: contributions as AnyObject[],
  };
}

function resolveCompositionContributions(contributions: AnyObject[]): JSONObject {
  let header: Fact<string> | undefined;
  const uses = new Map<string, Fact<string>>();
  const orderedUses: AnyObject[] = [];
  const wires: AnyObject[] = [];
  const wireSpans = new Map<string, IrSpan>();

  for (const contribution of contributions) {
    const {kind, target, value} = contribution;
    const span = contribution.span as IrSpan;
    const identity = contributionIdentity(contribution.id);
    if (
      !isObject(target) ||
      !isObject(value) ||
      !isObject(span) ||
      identity === null ||
      identity[2] !== 0
    ) {
      throw resolutionError("PN200", "invalid composition contribution", span);
    }
    if (identity[1] !== contribution.ordinal) {
      throw resolutionError("PN200", "invalid composition statement order", span);
    }
    if (header === undefined && kind !== "document.composition-header") {
      throw resolutionError("PN200", "composition header must be first", span);
    }
    if (!keysEqual(target, ["type"]) || target.type !== "document") {
      throw resolutionError("PN200", "invalid composition target", span);
    }

    if (kind === "document.composition-header") {
      const namespace = value.namespace;
      if (
        header !== undefined ||
        !keysEqual(value, ["namespace"]) ||
        typeof namespace !== "string" ||
        namespace.length === 0 ||
        containsIsolatedSurrogate(namespace)
      ) {
        throw resolutionError("PN200", "invalid composition header contribution", span);
      }
      header = {value: namespace, span};
      continue;
    }

    if (kind === "composition.use") {
      const {ref, alias} = value;
      if (
        !keysEqual(value, ["ref", "alias"]) ||
        typeof ref !== "string" ||
        ref.length === 0 ||
        typeof alias !== "string" ||
        alias.length === 0 ||
        !TIMER_BIND_IDENTIFIER.test(alias) ||
        containsIsolatedSurrogate(ref)
      ) {
        throw resolutionError("PN200", "invalid composition use", span);
      }
      const existing = uses.get(alias);
      if (existing !== undefined) {
        if (existing.value !== ref) {
          throw resolutionError(
            "PN202",
            `conflicting use facts for alias ${alias}`,
            span,
            "use each alias for exactly one referenced net",
            [[`first use of alias ${alias} was declared here`, existing.span]],
          );
        }
        continue;
      }
      uses.set(alias, {value: ref, span});
      orderedUses.push({ref, alias});
      continue;
    }

    if (kind === "composition.wire") {
      const rawFrom = value.from;
      const rawTo = value.to;
      if (!keysEqual(value, ["from", "to"]) || !isObject(rawFrom) || !isObject(rawTo)) {
        throw resolutionError("PN200", "invalid composition wire", span);
      }
      const endpointValues: [string, string][] = [];
      for (const endpoint of [rawFrom, rawTo]) {
        const {alias, place, span: endpointSpan} = endpoint;
        if (
          !keysEqual(endpoint, ["alias", "place", "span"]) ||
          typeof alias !== "string" ||
          alias.length === 0 ||
          typeof place !== "string" ||
          place.length === 0 ||
          !isSourceSpan(endpointSpan, identity[0])
        ) {
          throw resolutionError("PN200", "invalid composition wire", span);
        }
        if (!uses.has(alias)) {
          throw resolutionError(
            "PN202",
            `wire references unknown alias ${alias}`,
            endpointSpan,
            "declare the alias with use before wiring it",
          );
        }
        endpointValues.push([alias, place]);
      }
      const [source, destination] = endpointValues as [[string, string], [string, string]];
      const key = JSON.stringify([source, destination]);
      if (wireSpans.has(key)) continue;
      const reverse = JSON.stringify([destination, source]);
      const reverseSpan = wireSpans.get(reverse);
      if (reverseSpan !== undefined) {
        throw resolutionError(
          "PN202",
          "conflicting wire facts reverse the same endpoints",
          span,
          "remove the reversed wire; wires run output to input",
          [["first wire between these endpoints was declared here", reverseSpan]],
        );
      }
      wireSpans.set(key, span);
      wires.push({
        from: {net: source[0], port: source[1]},
        to: {net: destination[0], port: destination[1]},
      });
      continue;
    }

    throw resolutionError("PN200", `unsupported contribution kind ${pythonRepr(kind)}`, span);
  }

  if (header === undefined) throw resolutionError("PN200", "missing composition header");
  if (orderedUses.length === 0) {
    throw resolutionError(
      "PN202",
      "composition requires at least one use fact",
      header.span,
      'add `use "path" as alias` after the composition header',
    );
  }
  return {nets: orderedUses, wires};
}

let celAdapter: ReturnType<typeof createDefaultCelAdapter> | undefined;
function isValidCel(source: string): boolean {
  try {
    (celAdapter ??= createDefaultCelAdapter()).compile(source);
    return true;
  } catch {
    return false;
  }
}

/** Resolve closed v1 Contribution IR into canonical net or composition JSON. */
export function resolveContributionIr(ir: ContributionIr | unknown): Readonly<JSONObject> {
  const {documentKind, contributions} = validateEnvelope(ir);
  if (documentKind === "composition") return resolveCompositionContributions(contributions);

  let header: AnyObject | undefined;
  const placeAppearances: string[] = [];
  const declaredPlaces = new Set<string>();
  const transitions: string[] = [];
  const accepts = new Map<string, string[]>();
  const declaredAccepts = new Map<string, Fact<string[]>>();
  const ports = new Map<string, Fact<AnyObject>>();
  const handlers = new Map<string, Fact<string>>();
  const guards = new Map<string, Fact<string>>();
  const transitionSpans = new Map<string, IrSpan>();
  let arcs: ResolvedArc[] = [];
  const arcDeclarations: ArcDeclaration[] = [];
  const arcIndexes = new Map<string, number>();
  const arcObjects = new Map<string, ResolvedArc>();
  const arcHandles = new Map<string, Identity[]>();
  const arcHandleSpans = new Map<string, IrSpan>();
  const arcHandleIds = new Map<string, Identity>();
  const claimedHandleArcIds = new Set<string>();
  const arcData = new Map<string, Fact<JSONValue>>();
  const arcCels = new Map<string, Fact<string>>();
  const arcPredicates = new Map<string, Fact<AnyObject>>();
  const arcCorrelates = new Map<string, Fact<string>>();
  const capacities = new Map<string, Fact<AnyObject>>();
  const timers = new Map<string, {clock: string; cel: string; span: IrSpan}>();
  const timerBinds = new Map<string, Map<string, Fact<string>>>();
  const timerMaturities = new Map<string, Fact<string>>();
  const priorities = new Map<string, Fact<number>>();
  const arcWeights = new Map<string, Fact<number>>();
  const transitionOrders = new Map<string, number>();
  const transitionOrderRanks = new Map<number, Fact<string>>();
  const handleOrders = new Map<string, number>();
  const templates = new Map<string, JSONValue>();
  const templateSpans = new Map<string, IrSpan>();
  const markings: {place: string; token: AnyObject; count: number; span: IrSpan}[] = [];
  const descriptions = new Map<string, Fact<string>>();
  const annotations = new Map<string, Map<string, Fact<JSONValue>>>();
  const namedMarkings = new Map<string, {place: string; token: AnyObject; count: number; span: IrSpan}[]>();
  const positions = new Map<string, Map<string, Fact<AnyObject>>>();
  const routes = new Map<string, Fact<AnyObject[]>>();
  let extensions: Fact<JSONObject> | undefined;

  const metadataKey = (type: string, name: string): string => JSON.stringify([type, name]);
  const viewSubjectKey = (view: string, type: string): string => JSON.stringify([view, type]);
  const routeKey = (view: string, handle: string): string => JSON.stringify([view, handle]);
  const pushPlaceAppearance = (name: string): void => {
    if (!placeAppearances.includes(name)) placeAppearances.push(name);
  };
  const pushTransition = (name: string, span: IrSpan): void => {
    if (!transitions.includes(name)) {
      transitions.push(name);
      transitionSpans.set(name, span);
    }
  };

  for (const contribution of contributions) {
    const {kind} = contribution;
    const target = contribution.target as AnyObject;
    const value = contribution.value as AnyObject;
    const span = contribution.span as IrSpan;
    if (!isObject(target) || !isObject(value) || !isObject(span)) {
      throw resolutionError("PN200", "invalid Contribution IR member");
    }

    if (kind === "document.net-header") {
      if (
        header !== undefined ||
        !keysEqual(target, ["type"]) ||
        target.type !== "document" ||
        Object.keys(value).some((key) => key !== "name" && key !== "description") ||
        typeof value.name !== "string"
      ) {
        throw resolutionError("PN200", "invalid net header contribution", span);
      }
      header = value;
      continue;
    }

    if (kind === "place.declare") {
      const name = target.name;
      if (
        !keysEqual(target, ["type", "name"]) ||
        target.type !== "place" ||
        typeof name !== "string" ||
        name.length === 0 ||
        Object.keys(value).length !== 0
      ) {
        throw resolutionError("PN200", "invalid place declaration contribution", span);
      }
      declaredPlaces.add(name);
      pushPlaceAppearance(name);
      continue;
    }

    if (kind === "transition.declare") {
      const name = target.name;
      if (
        !keysEqual(target, ["type", "name"]) ||
        target.type !== "transition" ||
        typeof name !== "string" ||
        name.length === 0 ||
        Object.keys(value).length !== 0
      ) {
        throw resolutionError("PN200", "invalid transition declaration contribution", span);
      }
      pushTransition(name, span);
      continue;
    }

    if (kind === "place.accepts") {
      const name = target.name;
      const rawColors = value.colors;
      if (
        !keysEqual(target, ["type", "name"]) ||
        target.type !== "place" ||
        typeof name !== "string" ||
        name.length === 0 ||
        !keysEqual(value, ["colors"]) ||
        !Array.isArray(rawColors) ||
        rawColors.length === 0 ||
        rawColors.some((color) => typeof color !== "string" || color.length === 0) ||
        new Set(rawColors).size !== rawColors.length
      ) {
        throw resolutionError("PN200", "invalid place accepted-colors contribution", span);
      }
      const colors = rawColors as string[];
      pushPlaceAppearance(name);
      const existing = declaredAccepts.get(name);
      if (existing !== undefined) {
        if (!jsonValuesEqual(existing.value, colors)) {
          throw resolutionError(
            "PN202",
            `conflicting accepted-color facts for place (${name})`,
            span,
            "remove one declaration or make both accepted-color lists identical",
            [["first accepted-color declaration was here", existing.span]],
          );
        }
        continue;
      }
      declaredAccepts.set(name, {value: colors, span});
      continue;
    }

    if (kind === "place.port") {
      const name = target.name;
      const {direction, color} = value;
      if (
        !keysEqual(target, ["type", "name"]) ||
        target.type !== "place" ||
        typeof name !== "string" ||
        name.length === 0 ||
        !keysEqual(value, ["direction", "color"]) ||
        (direction !== "input" && direction !== "output") ||
        typeof color !== "string" ||
        color.length === 0
      ) {
        throw resolutionError("PN200", "invalid place port contribution", span);
      }
      const port = {direction, type: color};
      const existing = ports.get(name);
      if (existing !== undefined) {
        if (!jsonValuesEqual(existing.value, port)) {
          throw resolutionError(
            "PN202",
            `conflicting port facts for place (${name})`,
            span,
            "remove one declaration or make both port values identical",
            [["first port was declared here", existing.span]],
          );
        }
        continue;
      }
      ports.set(name, {value: port, span});
      continue;
    }

    if (kind === "arc.handle") {
      const name = target.name;
      const rawArcIds = value.arcIds;
      const contributionId = contributionIdentity(contribution.id);
      if (
        !keysEqual(target, ["type", "name"]) ||
        target.type !== "arcHandle" ||
        typeof name !== "string" ||
        !keysEqual(value, ["arcIds"]) ||
        !Array.isArray(rawArcIds) ||
        rawArcIds.length < 1 ||
        contributionId === null ||
        contributionId[2] !== 0
      ) {
        throw resolutionError("PN200", "invalid arc handle contribution", span);
      }
      const resolvedIds: Identity[] = [];
      for (const rawArcId of rawArcIds) {
        const arcId = arcIdentity(rawArcId);
        if (
          arcId === null ||
          arcId[0] !== contributionId[0] ||
          claimedHandleArcIds.has(identityKey(arcId))
        ) {
          throw resolutionError("PN200", "invalid arc handle contribution", span);
        }
        resolvedIds.push(arcId);
      }
      for (const id of resolvedIds) claimedHandleArcIds.add(identityKey(id));
      const existing = arcHandles.get(name);
      if (existing !== undefined) {
        if (!jsonValuesEqual(existing, resolvedIds)) {
          throw resolutionError(
            "PN202",
            `conflicting declarations for arc handle @${name}`,
            span,
            undefined,
            [["first declaration was here", arcHandleSpans.get(name) as IrSpan]],
          );
        }
        continue;
      }
      arcHandles.set(name, resolvedIds);
      arcHandleIds.set(name, contributionId);
      arcHandleSpans.set(name, span);
      continue;
    }

    if (kind === "order.transition") {
      const name = target.name;
      const rank = value.rank;
      if (
        !keysEqual(target, ["type", "name"]) ||
        target.type !== "transition" ||
        typeof name !== "string" ||
        !Number.isInteger(rank) ||
        rank < 1 ||
        transitionOrders.has(name)
      ) {
        throw resolutionError("PN200", "invalid transition order contribution", span);
      }
      const existing = transitionOrderRanks.get(rank);
      if (existing !== undefined) {
        throw resolutionError(
          "PN202",
          `transition order position ${rank} is assigned more than once`,
          span,
          "assign each explicitly ordered transition a unique positive position",
          [[`[${existing.value}] first assigned position ${rank}`, existing.span]],
        );
      }
      transitionOrders.set(name, rank);
      transitionOrderRanks.set(rank, {value: name, span});
      continue;
    }

    if (kind === "order.arc-run") {
      const name = target.name;
      const rank = value.rank;
      if (
        !keysEqual(target, ["type", "name"]) ||
        target.type !== "arcHandle" ||
        typeof name !== "string" ||
        !Number.isInteger(rank) ||
        rank < 1 ||
        handleOrders.has(name)
      ) {
        throw resolutionError("PN200", "invalid arc-run order contribution", span);
      }
      handleOrders.set(name, rank);
      continue;
    }

    if (kind === "place.capacity-per-color-key") {
      const name = target.name;
      if (!keysEqual(target, ["type", "name"]) || target.type !== "place" || typeof name !== "string") {
        throw resolutionError("PN200", "invalid place capacity contribution", span);
      }
      if (!keysEqual(value, ["key", "max"])) {
        throw resolutionError(
          "PN202",
          `capacityPerColorKey for (${name}) must contain exactly key and max`,
          span,
        );
      }
      if (!validCapacityKey(value.key)) {
        throw resolutionError(
          "PN202",
          `capacityPerColorKey key must be a non-empty string or non-empty array of non-empty strings for (${name})`,
          span,
        );
      }
      if (!Number.isInteger(value.max) || value.max < 1) {
        throw resolutionError(
          "PN202",
          `capacityPerColorKey max must be an integer greater than or equal to 1; got ${pythonRepr(value.max)} for (${name})`,
          span,
        );
      }
      const capacity = {key: value.key, max: value.max};
      const existing = capacities.get(name);
      if (existing !== undefined) {
        if (!jsonValuesEqual(existing.value, capacity)) {
          throw resolutionError(
            "PN202",
            `conflicting capacityPerColorKey facts for (${name})`,
            span,
            undefined,
            [["first declaration was here", existing.span]],
          );
        }
        continue;
      }
      capacities.set(name, {value: capacity, span});
      continue;
    }

    if (kind === "arc.weight") {
      const name = target.name;
      const weight = value.weight;
      if (
        !keysEqual(target, ["type", "name"]) ||
        target.type !== "arcHandle" ||
        typeof name !== "string" ||
        !keysEqual(value, ["weight"])
      ) {
        throw resolutionError("PN200", "invalid arc weight contribution", span);
      }
      if (!Number.isInteger(weight) || weight < 1) {
        throw resolutionError(
          "PN202",
          `arc weight must be an integer greater than or equal to 1; got ${pythonRepr(weight)} for @${name}`,
          span,
        );
      }
      const existing = arcWeights.get(name);
      if (existing !== undefined) {
        if (existing.value !== weight) {
          throw resolutionError(
            "PN202",
            `conflicting weight facts for arc @${name}`,
            span,
            undefined,
            [["first declaration was here", existing.span]],
          );
        }
        continue;
      }
      arcWeights.set(name, {value: weight, span});
      continue;
    }

    if (kind === "arc.produce-data") {
      const name = target.name;
      if (
        !keysEqual(target, ["type", "name"]) ||
        target.type !== "arcHandle" ||
        typeof name !== "string" ||
        !keysEqual(value, ["data"]) ||
        !isObject(value.data)
      ) {
        throw resolutionError("PN200", "invalid arc produce-data contribution", span);
      }
      let data: JSONValue;
      try {
        data = untag(value.data);
      } catch (error) {
        if (error instanceof PetrinetResourceError) throw error;
        throw resolutionError("PN200", "invalid tagged JSON arc data value", span);
      }
      const existing = arcData.get(name);
      if (existing !== undefined) {
        if (!jsonValuesEqual(existing.value, data)) {
          throw resolutionError(
            "PN202",
            `conflicting data facts for arc @${name}`,
            span,
            "remove one declaration or make both arc data values identical",
            [[`first value ${compactSortedJson(existing.value)} was declared here`, existing.span]],
          );
        }
        continue;
      }
      arcData.set(name, {value: data, span});
      continue;
    }

    if (kind === "arc.produce-cel") {
      const name = target.name;
      const cel = value.cel;
      if (
        !keysEqual(target, ["type", "name"]) ||
        target.type !== "arcHandle" ||
        typeof name !== "string" ||
        !keysEqual(value, ["cel"]) ||
        typeof cel !== "string" ||
        cel.length === 0
      ) {
        throw resolutionError("PN200", "invalid arc produce-cel contribution", span);
      }
      const existing = arcCels.get(name);
      if (existing !== undefined) {
        if (existing.value !== cel) {
          throw resolutionError(
            "PN202",
            `conflicting data cel facts for arc @${name}`,
            span,
            undefined,
            [["first declaration was here", existing.span]],
          );
        }
        continue;
      }
      arcCels.set(name, {value: cel, span});
      continue;
    }

    if (kind === "arc.predicate") {
      const name = target.name;
      const predicateKind = value.kind;
      if (
        !keysEqual(target, ["type", "name"]) ||
        target.type !== "arcHandle" ||
        typeof name !== "string" ||
        (predicateKind !== "cel" && predicateKind !== "handler") ||
        !keysEqual(value, ["kind", predicateKind]) ||
        typeof value[predicateKind] !== "string" ||
        value[predicateKind].length === 0
      ) {
        throw resolutionError("PN200", "invalid arc predicate contribution", span);
      }
      const predicate = {[predicateKind]: value[predicateKind]};
      const existing = arcPredicates.get(name);
      if (existing !== undefined) {
        if (!jsonValuesEqual(existing.value, predicate)) {
          throw resolutionError(
            "PN202",
            `conflicting predicate facts for arc @${name}`,
            span,
            "remove one declaration or make both predicate values identical",
            [["first predicate was declared here", existing.span]],
          );
        }
        continue;
      }
      arcPredicates.set(name, {value: predicate, span});
      continue;
    }

    if (kind === "arc.correlate") {
      const name = target.name;
      const cel = value.cel;
      if (
        !keysEqual(target, ["type", "name"]) ||
        target.type !== "arcHandle" ||
        typeof name !== "string" ||
        !keysEqual(value, ["cel"]) ||
        typeof cel !== "string" ||
        cel.length === 0
      ) {
        throw resolutionError("PN200", "invalid arc correlate contribution", span);
      }
      const existing = arcCorrelates.get(name);
      if (existing !== undefined) {
        if (existing.value !== cel) {
          throw resolutionError(
            "PN202",
            `conflicting correlate facts for arc @${name}`,
            span,
            "remove one declaration or make both CEL expressions identical",
            [["first correlate was declared here", existing.span]],
          );
        }
        continue;
      }
      arcCorrelates.set(name, {value: cel, span});
      continue;
    }

    if (kind === "arc.declare") {
      const rawColor = value.color;
      const rawSource = value.from;
      const rawDestination = value.to;
      const mode = value.mode;
      const id = arcIdentity(target.id);
      const contributionId = contributionIdentity(contribution.id);
      if (
        !keysEqual(target, ["type", "id"]) ||
        target.type !== "arc" ||
        id === null ||
        contributionId === null ||
        !sameIdentity(id, contributionId) ||
        !isObject(rawColor) ||
        !isObject(rawSource) ||
        !isObject(rawDestination) ||
        !["consume", "read", "inhibit", "produce"].includes(mode)
      ) {
        throw resolutionError("PN200", "invalid arc declaration", span);
      }
      if (
        !keysEqual(rawColor, ["kind", "value"]) ||
        rawColor.kind !== "explicit" ||
        typeof rawColor.value !== "string" ||
        rawColor.value.length === 0
      ) {
        throw resolutionError("PN200", "invalid arc declaration", span);
      }
      let place: unknown;
      let transition: unknown;
      let transitionNameSpan: IrSpan;
      const isInput =
        rawSource.type === "place" &&
        rawDestination.type === "transition" &&
        ["consume", "read", "inhibit"].includes(mode);
      if (isInput) {
        const expected = ["from", "to", "color", "mode"];
        if (mode === "consume") expected.push("transitionNameSpan");
        if (!keysEqual(value, expected)) {
          throw resolutionError("PN200", "invalid consumed arc declaration", span);
        }
        place = rawSource.name;
        transition = rawDestination.name;
        transitionNameSpan = value.transitionNameSpan ?? span;
        if (mode === "consume" && !isSourceSpan(transitionNameSpan, contributionId[0])) {
          throw resolutionError("PN200", "consumed arc has invalid transitionNameSpan", span);
        }
      } else if (
        rawSource.type === "transition" &&
        rawDestination.type === "place" &&
        mode === "produce"
      ) {
        if (!keysEqual(value, ["from", "to", "color", "mode"])) {
          throw resolutionError("PN200", "invalid produced arc declaration", span);
        }
        place = rawDestination.name;
        transition = rawSource.name;
        transitionNameSpan = span;
      } else {
        throw resolutionError("PN200", "invalid Coin Deposit arc direction", span);
      }
      if (typeof place !== "string" || typeof transition !== "string") {
        throw resolutionError("PN200", "arc endpoint names must be strings", span);
      }
      pushPlaceAppearance(place);
      pushTransition(transition, transitionNameSpan);
      arcDeclarations.push({
        id,
        place,
        transition,
        isInput,
        mode,
        color: rawColor as {kind: "explicit"; value: string},
        span,
        transitionNameSpan,
      });
      continue;
    }

    if (kind === "transition.timer") {
      const name = target.name;
      const rawClock = value.clock;
      const cel = value.cel;
      if (
        !isObject(rawClock) ||
        !keysEqual(target, ["type", "name"]) ||
        target.type !== "transition" ||
        typeof name !== "string" ||
        name.length === 0 ||
        !keysEqual(value, ["clock", "cel"]) ||
        !keysEqual(rawClock, ["type", "name"]) ||
        rawClock.type !== "place" ||
        typeof rawClock.name !== "string" ||
        rawClock.name.length === 0 ||
        typeof cel !== "string" ||
        cel.length === 0
      ) {
        throw resolutionError("PN200", "invalid transition timer contribution", span);
      }
      const existing = timers.get(name);
      if (existing !== undefined) {
        if (existing.clock !== rawClock.name) {
          throw resolutionError(
            "PN202",
            `conflicting timer clock facts for transition [${name}]`,
            span,
            "remove one declaration or make both timer clock values identical",
            [["first timer clock was declared here", existing.span]],
          );
        }
        if (existing.cel !== cel) {
          throw resolutionError(
            "PN202",
            `conflicting timer CEL facts for transition [${name}]`,
            span,
            "remove one declaration or make both timer CEL values identical",
            [["first timer CEL was declared here", existing.span]],
          );
        }
        continue;
      }
      timers.set(name, {clock: rawClock.name, cel, span});
      continue;
    }

    if (kind === "transition.timer-maturity") {
      const name = target.name;
      const maturity = value.maturity;
      if (
        !keysEqual(target, ["type", "name"]) ||
        target.type !== "transition" ||
        typeof name !== "string" ||
        name.length === 0 ||
        !keysEqual(value, ["maturity"]) ||
        typeof maturity !== "string" ||
        maturity.length === 0
      ) {
        throw resolutionError("PN200", "invalid transition timer maturity contribution", span);
      }
      const existing = timerMaturities.get(name);
      if (existing !== undefined && existing.value !== maturity) {
        throw resolutionError(
          "PN202",
          `conflicting timer maturity CEL facts for transition [${name}]`,
          span,
          "remove one declaration or make both timer maturity CEL values identical",
          [["first timer maturity CEL was declared here", existing.span]],
        );
      }
      if (existing === undefined) timerMaturities.set(name, {value: maturity, span});
      continue;
    }

    if (kind === "transition.timer-bind") {
      const name = target.name;
      const bindName = value.name;
      const rawPlace = value.place;
      if (
        !isObject(rawPlace) ||
        !keysEqual(target, ["type", "name"]) ||
        target.type !== "transition" ||
        typeof name !== "string" ||
        name.length === 0 ||
        !keysEqual(value, ["name", "place"]) ||
        typeof bindName !== "string" ||
        !TIMER_BIND_IDENTIFIER.test(bindName) ||
        !keysEqual(rawPlace, ["type", "name"]) ||
        rawPlace.type !== "place" ||
        typeof rawPlace.name !== "string" ||
        rawPlace.name.length === 0
      ) {
        throw resolutionError("PN200", "invalid transition timer bind contribution", span);
      }
      let binds = timerBinds.get(name);
      if (binds === undefined) {
        binds = new Map();
        timerBinds.set(name, binds);
      }
      const existing = binds.get(bindName);
      if (existing !== undefined) {
        if (existing.value !== rawPlace.name) {
          throw resolutionError(
            "PN202",
            `conflicting timer bind facts for ${pythonRepr(bindName)} on transition [${name}]`,
            span,
            "remove one declaration or make both timer bind values identical",
            [["first timer bind was declared here", existing.span]],
          );
        }
        continue;
      }
      binds.set(bindName, {value: rawPlace.name, span});
      continue;
    }

    if (kind === "transition.priority") {
      const name = target.name;
      const priority = value.priority;
      if (
        !keysEqual(target, ["type", "name"]) ||
        target.type !== "transition" ||
        typeof name !== "string" ||
        name.length === 0 ||
        !keysEqual(value, ["priority"]) ||
        !Number.isInteger(priority) ||
        priority < 0
      ) {
        throw resolutionError("PN200", "invalid transition priority contribution", span);
      }
      const existing = priorities.get(name);
      if (existing !== undefined) {
        if (existing.value !== priority) {
          throw resolutionError(
            "PN202",
            `conflicting priority facts for transition [${name}]`,
            span,
            "remove one declaration or make both priority values identical",
            [["first priority was declared here", existing.span]],
          );
        }
        continue;
      }
      priorities.set(name, {value: priority, span});
      continue;
    }

    if (kind === "transition.handler") {
      const name = target.name;
      const handler = value.handler;
      if (
        !keysEqual(target, ["type", "name"]) ||
        target.type !== "transition" ||
        typeof name !== "string" ||
        name.length === 0 ||
        !keysEqual(value, ["handler"]) ||
        typeof handler !== "string"
      ) {
        throw resolutionError("PN200", "invalid transition handler contribution", span);
      }
      const existing = handlers.get(name);
      if (existing !== undefined) {
        if (existing.value !== handler) {
          throw resolutionError(
            "PN202",
            `conflicting handler facts for transition [${name}]`,
            span,
            "remove one declaration or make both handler values identical",
            [["first handler was declared here", existing.span]],
          );
        }
        continue;
      }
      handlers.set(name, {value: handler, span});
      continue;
    }

    if (kind === "transition.guard") {
      const name = target.name;
      const guard = value.guard;
      if (
        !keysEqual(target, ["type", "name"]) ||
        target.type !== "transition" ||
        typeof name !== "string" ||
        name.length === 0 ||
        !keysEqual(value, ["guard"]) ||
        typeof guard !== "string" ||
        guard.length === 0
      ) {
        throw resolutionError("PN200", "invalid transition guard contribution", span);
      }
      const existing = guards.get(name);
      if (existing !== undefined) {
        if (existing.value !== guard) {
          throw resolutionError(
            "PN204",
            `conflicting guard facts for transition [${name}]`,
            span,
            "remove one declaration or make both guard values identical",
            [["first guard was declared here", existing.span]],
          );
        }
        continue;
      }
      guards.set(name, {value: guard, span});
      continue;
    }

    if (kind === "marking.append") {
      const place = target.name;
      const count = value.count;
      const message = "invalid initial marking contribution";
      if (
        !keysEqual(target, ["type", "name"]) ||
        target.type !== "place" ||
        typeof place !== "string" ||
        place.length === 0 ||
        !keysEqual(value, ["count", "token"]) ||
        !Number.isInteger(count) ||
        count < 1
      ) {
        throw resolutionError("PN200", message, span);
      }
      markings.push({place, token: parseMarkingToken(value.token, message, span), count, span});
      continue;
    }

    if (kind === "template.define") {
      const name = target.name;
      if (
        !keysEqual(target, ["type", "name"]) ||
        target.type !== "template" ||
        typeof name !== "string" ||
        !isObject(value.value)
      ) {
        throw resolutionError("PN200", "invalid template definition contribution", span);
      }
      let resolvedTemplate: JSONValue;
      try {
        resolvedTemplate = untag(value.value);
      } catch (error) {
        if (error instanceof PetrinetResourceError) throw error;
        throw resolutionError("PN200", "invalid tagged JSON template value", span);
      }
      if (templates.has(name)) {
        if (!jsonValuesEqual(templates.get(name), resolvedTemplate)) {
          throw resolutionError(
            "PN202",
            `conflicting definitions for template $${name}`,
            span,
            undefined,
            [["first definition was here", templateSpans.get(name) as IrSpan]],
          );
        }
        continue;
      }
      templates.set(name, resolvedTemplate);
      templateSpans.set(name, span);
      continue;
    }

    if (kind === "documentation.description") {
      const targetType = target.type;
      const targetName = target.name ?? "";
      const text = value.text;
      if (
        (!["document", "place", "transition", "arcHandle"].includes(targetType)) ||
        (targetType === "document" &&
          !(
            keysEqual(target, ["type"]) ||
            (keysEqual(target, ["type", "kind"]) && target.kind === "net")
          )) ||
        (targetType !== "document" && (!keysEqual(target, ["type", "name"]) || typeof targetName !== "string")) ||
        !keysEqual(value, ["text"]) ||
        typeof text !== "string"
      ) {
        throw resolutionError("PN200", "invalid documentation description contribution", span);
      }
      const key = metadataKey(targetType, targetName);
      const existing = descriptions.get(key);
      if (existing !== undefined && existing.value !== text) {
        const renderedTarget =
          targetType === "place" ? `(${targetName})` :
          targetType === "transition" ? `[${targetName}]` :
          targetType === "arcHandle" ? `@${targetName}` : "net";
        throw resolutionError(
          "PN202",
          `conflicting description facts for ${targetType} ${renderedTarget}`,
          span,
          undefined,
          [["first description was declared here", existing.span]],
        );
      }
      if (existing === undefined) descriptions.set(key, {value: text, span});
      continue;
    }

    if (kind === "documentation.annotation") {
      const targetType = target.type;
      const targetName = target.name ?? "";
      const annotationKey = value.key;
      if (
        !["document", "place", "transition", "arcHandle"].includes(targetType) ||
        (targetType === "document" &&
          !(
            keysEqual(target, ["type"]) ||
            (keysEqual(target, ["type", "kind"]) && target.kind === "net")
          )) ||
        (targetType !== "document" && (!keysEqual(target, ["type", "name"]) || typeof targetName !== "string")) ||
        !keysEqual(value, ["key", "value"]) ||
        typeof annotationKey !== "string" ||
        annotationKey.length === 0 ||
        !isObject(value.value)
      ) {
        throw resolutionError("PN200", "invalid documentation annotation contribution", span);
      }
      if (annotationKey === "petrinet.dsl/v1") {
        throw resolutionError(
          "PN202",
          "annotation key 'petrinet.dsl/v1' is reserved for compiler-owned metadata",
          span,
          "use extensions for opaque full-document data",
        );
      }
      let annotationValue: JSONValue;
      try {
        annotationValue = untag(value.value);
      } catch (error) {
        if (error instanceof PetrinetResourceError) throw error;
        throw resolutionError("PN200", "invalid documentation annotation contribution", span);
      }
      const identity = metadataKey(targetType, targetName);
      let facts = annotations.get(identity);
      if (facts === undefined) {
        facts = new Map();
        annotations.set(identity, facts);
      }
      const existing = facts.get(annotationKey);
      if (existing !== undefined && !jsonValuesEqual(existing.value, annotationValue)) {
        const renderedTarget =
          targetType === "place" ? `place (${targetName})` :
          targetType === "transition" ? `transition [${targetName}]` :
          targetType === "arcHandle" ? `arc @${targetName}` : "net";
        throw resolutionError(
          "PN202",
          `conflicting annotation ${annotationKey} facts for ${renderedTarget}`,
          span,
          "remove one declaration or make both annotation values identical",
          [["first annotation value was declared here", existing.span]],
        );
      }
      if (existing === undefined) facts.set(annotationKey, {value: annotationValue, span});
      continue;
    }

    if (kind === "metadata.named-marking") {
      const name = value.name;
      const rawEntries = value.entries;
      if (
        !keysEqual(target, ["type"]) ||
        target.type !== "document" ||
        !keysEqual(value, ["name", "entries"]) ||
        typeof name !== "string" ||
        name.length === 0 ||
        !Array.isArray(rawEntries)
      ) {
        throw resolutionError("PN200", "invalid named marking contribution", span);
      }
      if (name === "initial") {
        throw resolutionError(
          "PN202",
          "'initial' is reserved and cannot name a non-initial marking",
          span,
          "use the unquoted initial marking keyword or choose another name",
        );
      }
      const parsedEntries: {place: string; token: AnyObject; count: number; span: IrSpan}[] = [];
      for (const rawEntry of rawEntries) {
        if (!isObject(rawEntry) || !isObject(rawEntry.place)) {
          throw resolutionError("PN200", "invalid named marking contribution", span);
        }
        const place = rawEntry.place.name;
        const count = rawEntry.count;
        if (
          !keysEqual(rawEntry, ["place", "count", "token"]) ||
          !keysEqual(rawEntry.place, ["type", "name"]) ||
          rawEntry.place.type !== "place" ||
          typeof place !== "string" ||
          place.length === 0 ||
          !Number.isInteger(count) ||
          count < 1
        ) {
          throw resolutionError("PN200", "invalid named marking contribution", span);
        }
        parsedEntries.push({
          place,
          token: parseMarkingToken(rawEntry.token, "invalid named marking contribution", span),
          count,
          span,
        });
      }
      const entries = namedMarkings.get(name) ?? [];
      entries.push(...parsedEntries);
      namedMarkings.set(name, entries);
      continue;
    }

    if (kind === "view.position") {
      const viewName = target.name;
      const subject = value.subject;
      if (
        !keysEqual(target, ["type", "name"]) ||
        target.type !== "view" ||
        typeof viewName !== "string" ||
        viewName.length === 0 ||
        !keysEqual(value, ["subject", "position"]) ||
        !isObject(subject) ||
        (subject.type !== "place" && subject.type !== "transition") ||
        !keysEqual(subject, ["type", "name"]) ||
        typeof subject.name !== "string" ||
        !isObject(value.position)
      ) {
        throw resolutionError("PN200", "invalid view position contribution", span);
      }
      let decodedPosition: JSONValue;
      try {
        decodedPosition = untag(value.position);
      } catch (error) {
        if (error instanceof PetrinetResourceError) throw error;
        throw resolutionError("PN200", "invalid view position contribution", span);
      }
      if (!isObject(decodedPosition)) {
        throw resolutionError("PN200", "invalid view position contribution", span);
      }
      const position = decodedPosition as AnyObject;
      if (
        !keysEqual(position, ["x", "y"]) ||
        typeof position.x !== "number" ||
        typeof position.y !== "number"
      ) {
        throw resolutionError("PN200", "invalid view position contribution", span);
      }
      const subjectKey = `${subject.type}:${subject.name}`;
      const key = viewSubjectKey(viewName, subject.type);
      let viewPositions = positions.get(key);
      if (viewPositions === undefined) {
        viewPositions = new Map();
        positions.set(key, viewPositions);
      }
      const existing = viewPositions.get(subjectKey);
      if (existing !== undefined && !jsonValuesEqual(existing.value, position)) {
        throw resolutionError(
          "PN202",
          `conflicting position facts for ${subject.type} ${subject.type === "place" ? `(${subject.name})` : `[${subject.name}]`} in view ${pythonRepr(viewName)}`,
          span,
          "remove one declaration or make both positions identical",
          [["first position was declared here", existing.span]],
        );
      }
      if (existing === undefined) viewPositions.set(subjectKey, {value: position, span});
      continue;
    }

    if (kind === "view.route") {
      const handle = target.name;
      const view = value.view;
      const rawPoints = value.points;
      if (
        !keysEqual(target, ["type", "name"]) ||
        target.type !== "arcHandle" ||
        typeof handle !== "string" ||
        !keysEqual(value, ["view", "points"]) ||
        !isObject(view) ||
        !keysEqual(view, ["type", "name"]) ||
        view.type !== "view" ||
        typeof view.name !== "string" ||
        !Array.isArray(rawPoints)
      ) {
        throw resolutionError("PN200", "invalid view route contribution", span);
      }
      if (rawPoints.length === 0) {
        throw resolutionError(
          "PN202",
          `view ${pythonRepr(view.name)} route @${handle} requires at least one point`,
          span,
          'add at least one {"x": Number, "y": Number} point',
        );
      }
      const points: AnyObject[] = [];
      try {
        for (const rawPoint of rawPoints) {
          if (!isObject(rawPoint)) throw new Error("invalid point");
          const decodedPoint = untag(rawPoint);
          if (!isObject(decodedPoint)) throw new Error("invalid point");
          const point = decodedPoint as AnyObject;
          if (
            !keysEqual(point, ["x", "y"]) ||
            typeof point.x !== "number" ||
            typeof point.y !== "number"
          ) {
            throw new Error("invalid point");
          }
          points.push(point);
        }
      } catch (error) {
        if (error instanceof PetrinetResourceError) throw error;
        throw resolutionError("PN200", "invalid view route contribution", span);
      }
      const key = routeKey(view.name, handle);
      const existing = routes.get(key);
      if (existing !== undefined && !jsonValuesEqual(existing.value, points)) {
        throw resolutionError(
          "PN202",
          `conflicting route facts for arc @${handle} in view ${pythonRepr(view.name)}`,
          span,
          undefined,
          [["first route was declared here", existing.span]],
        );
      }
      if (existing === undefined) routes.set(key, {value: points, span});
      continue;
    }

    if (kind === "document.extensions") {
      if (
        !keysEqual(target, ["type"]) ||
        target.type !== "document" ||
        !keysEqual(value, ["extensions"]) ||
        !isObject(value.extensions)
      ) {
        throw resolutionError("PN200", "invalid document extensions contribution", span);
      }
      let decodedValue: JSONValue;
      try {
        decodedValue = untag(value.extensions);
      } catch (error) {
        if (error instanceof PetrinetResourceError) throw error;
        throw resolutionError("PN200", "invalid document extensions contribution", span);
      }
      if (!isObject(decodedValue)) {
        throw resolutionError("PN200", "invalid document extensions contribution", span);
      }
      const decoded = decodedValue as JSONObject;
      if (extensions !== undefined && !jsonValuesEqual(extensions.value, decoded)) {
        throw resolutionError(
          "PN202",
          "conflicting document extensions facts",
          span,
          undefined,
          [["first extensions were declared here", extensions.span]],
        );
      }
      if (extensions === undefined) extensions = {value: decoded, span};
      continue;
    }

    throw resolutionError("PN200", `unsupported contribution kind ${pythonRepr(kind)}`, span);
  }

  const places = [...placeAppearances];
  for (const place of placeAppearances) {
    const declaration = declaredAccepts.get(place);
    if (declaration !== undefined) accepts.set(place, [...declaration.value]);
  }

  for (const declaration of arcDeclarations) {
    const {id, place, transition, isInput, mode, span, color} = declaration;
    const resolvedColor = color.value;
    let accepted = accepts.get(place);
    if (accepted === undefined) {
      accepted = [];
      accepts.set(place, accepted);
    }
    const declared = declaredAccepts.get(place);
    if (declared !== undefined && !declared.value.includes(resolvedColor)) {
      throw resolutionError(
        "PN202",
        `arc color ${pythonRepr(resolvedColor)} conflicts with accepted colors declared for (${place})`,
        span,
        "add the arc color to the place declaration or change the arc color",
        [["accepted colors were declared here", declared.span]],
      );
    }
    if (!accepted.includes(resolvedColor)) accepted.push(resolvedColor);
    const arc: ResolvedArc = {
      from: isInput ? {place} : {transition},
      to: isInput ? {transition} : {place},
    };
    if (isInput) {
      const consume: AnyObject = {type: resolvedColor};
      if (mode !== "consume") consume.mode = mode;
      arc.consume = consume;
    } else {
      arc.produce = {destination: place, type: resolvedColor};
    }
    const key = identityKey(id);
    if (arcIndexes.has(key)) throw resolutionError("PN200", "duplicate arc identity", span);
    arcIndexes.set(key, arcs.length);
    arcs.push(arc);
    arcObjects.set(key, arc);
  }

  for (const place of declaredPlaces) {
    if (!accepts.has(place)) accepts.set(place, ["token"]);
  }

  for (const [handle, resolvedIds] of arcHandles) {
    const handleId = arcHandleIds.get(handle) as Identity;
    let declarationCount = 0;
    for (const key of arcIndexes.keys()) {
      const id = JSON.parse(key) as Identity;
      if (id[0] === handleId[0] && id[1] === handleId[1]) declarationCount += 1;
    }
    const expectedIds: Identity[] = [];
    for (let part = 1; part <= declarationCount; part += 1) {
      expectedIds.push([handleId[0], handleId[1], part]);
    }
    if (!jsonValuesEqual(resolvedIds, expectedIds)) {
      throw resolutionError("PN200", "invalid arc handle contribution", arcHandleSpans.get(handle));
    }
    if (resolvedIds.some((id) => !arcIndexes.has(identityKey(id)))) {
      throw resolutionError(
        "PN202",
        `handle @${handle} refers to an unknown arc`,
        arcHandleSpans.get(handle),
      );
    }
  }

  for (const [place, fact] of capacities) {
    if (!accepts.has(place)) {
      throw resolutionError("PN202", `capacity refers to unknown place (${place})`, fact.span);
    }
  }

  for (const [place, fact] of ports) {
    const accepted = accepts.get(place);
    if (accepted === undefined) {
      throw resolutionError("PN202", `port refers to unknown place (${place})`, fact.span);
    }
    if (!accepted.includes(fact.value.type)) {
      throw resolutionError(
        "PN202",
        `port color ${pythonRepr(fact.value.type)} is not accepted by place (${place})`,
        fact.span,
        "declare a port color already accepted by the place",
      );
    }
  }

  for (const [handle, fact] of arcWeights) {
    const ids = arcHandles.get(handle);
    if (ids === undefined) {
      throw resolutionError("PN202", `weight refers to unknown arc handle @${handle}`, fact.span);
    }
    const inputIndexes = ids
      .map((id) => arcIndexes.get(identityKey(id)) as number)
      .filter((index) => Object.hasOwn(arcs[index] as AnyObject, "consume"));
    if (inputIndexes.length === 0) {
      throw resolutionError("PN202", `arc weight is not allowed on produce arc @${handle}`, fact.span);
    }
    if (inputIndexes.length !== 1) {
      throw resolutionError(
        "PN202",
        `arc handle @${handle} must identify exactly one input arc for weight`,
        fact.span,
      );
    }
    const consume = (arcs[inputIndexes[0] as number] as AnyObject).consume as AnyObject;
    if (consume.mode === "inhibit" && fact.value > 1) {
      throw resolutionError(
        "PN202",
        `arc weight greater than 1 is not allowed on inhibit arc @${handle}`,
        fact.span,
      );
    }
    if (fact.value > 1) consume.weight = fact.value;
  }

  for (const [handle, fact] of arcData) {
    const ids = arcHandles.get(handle);
    if (ids === undefined) {
      throw resolutionError("PN202", `data refers to unknown arc handle @${handle}`, fact.span);
    }
    const produceIndexes = ids
      .map((id) => arcIndexes.get(identityKey(id)) as number)
      .filter((index) => Object.hasOwn(arcs[index] as AnyObject, "produce"));
    if (produceIndexes.length !== 1) {
      throw resolutionError(
        "PN202",
        `arc handle @${handle} must identify exactly one produce arc`,
        fact.span,
      );
    }
    (arcs[produceIndexes[0] as number] as AnyObject).produce.data = fact.value;
  }

  for (const [handle, fact] of arcCels) {
    const literal = arcData.get(handle);
    if (literal !== undefined) {
      throw resolutionError(
        "PN202",
        `arc @${handle} declares both data and data cel; they are mutually exclusive`,
        fact.span,
        undefined,
        [["literal data was declared here", literal.span]],
      );
    }
    const ids = arcHandles.get(handle);
    if (ids === undefined) {
      throw resolutionError("PN202", `data cel refers to unknown arc handle @${handle}`, fact.span);
    }
    const produceIndexes = ids
      .map((id) => arcIndexes.get(identityKey(id)) as number)
      .filter((index) => Object.hasOwn(arcs[index] as AnyObject, "produce"));
    if (produceIndexes.length !== 1) {
      throw resolutionError(
        "PN202",
        `arc handle @${handle} must identify exactly one produce arc`,
        fact.span,
      );
    }
    if (!isValidCel(fact.value)) {
      throw resolutionError(
        "PN203",
        `invalid CEL data expression for arc @${handle}`,
        fact.span,
        "fix the CEL expression syntax",
      );
    }
    (arcs[produceIndexes[0] as number] as AnyObject).produce.cel = fact.value;
  }

  for (const [handle, fact] of arcPredicates) {
    const ids = arcHandles.get(handle);
    if (ids === undefined) {
      throw resolutionError("PN202", `predicate refers to unknown arc handle @${handle}`, fact.span);
    }
    const consumeIndexes = ids
      .map((id) => arcIndexes.get(identityKey(id)) as number)
      .filter((index) => Object.hasOwn(arcs[index] as AnyObject, "consume"));
    if (consumeIndexes.length !== 1) {
      throw resolutionError(
        "PN202",
        `arc handle @${handle} must identify exactly one consume arc`,
        fact.span,
      );
    }
    if (typeof fact.value.cel === "string" && !isValidCel(fact.value.cel)) {
      throw resolutionError(
        "PN203",
        `invalid CEL predicate for arc @${handle}`,
        fact.span,
        "fix the CEL expression syntax",
      );
    }
    (arcs[consumeIndexes[0] as number] as AnyObject).consume.predicate = fact.value;
  }

  for (const [handle, fact] of arcCorrelates) {
    const ids = arcHandles.get(handle);
    if (ids === undefined) {
      throw resolutionError("PN202", `correlate refers to unknown arc handle @${handle}`, fact.span);
    }
    const inhibitIndexes = ids
      .map((id) => arcIndexes.get(identityKey(id)) as number)
      .filter((index) => (arcs[index] as AnyObject).consume?.mode === "inhibit");
    if (inhibitIndexes.length !== 1 || ids.length !== 1) {
      const modes = ids.map((id) => {
        const arc = arcs[arcIndexes.get(identityKey(id)) as number] as AnyObject;
        return arc.consume === undefined ? "produce" : (arc.consume.mode ?? "consume");
      });
      const mode = modes.length === 1 ? modes[0] : "multiple arcs";
      const operator = mode === "consume" ? "-> (consume)" : mode === "read" ? "->? (read)" : mode === "produce" ? "-> (produce)" : mode;
      throw resolutionError(
        "PN202",
        `correlate is only allowed on ->0 inhibit arcs; @${handle} uses ${operator}`,
        fact.span,
        "move correlate to a named ->0 inhibit arc",
        [[`arc @${handle} was declared here`, arcHandleSpans.get(handle) as IrSpan]],
      );
    }
    if (!isValidCel(fact.value)) {
      throw resolutionError(
        "PN203",
        `invalid CEL correlate for arc @${handle}`,
        fact.span,
        "fix the CEL expression syntax",
      );
    }
    (arcs[inhibitIndexes[0] as number] as AnyObject).consume.correlate = {cel: fact.value};
  }

  for (const [transition, fact] of guards) {
    if (!transitions.includes(transition)) {
      throw resolutionError("PN202", `guard refers to unknown transition [${transition}]`, fact.span);
    }
  }
  for (const [transition, fact] of timers) {
    if (!transitions.includes(transition)) {
      throw resolutionError("PN202", `timer refers to unknown transition [${transition}]`, fact.span);
    }
  }
  for (const [transition, fact] of timerMaturities) {
    if (!transitions.includes(transition)) {
      throw resolutionError(
        "PN202",
        `timer maturity refers to unknown transition [${transition}]`,
        fact.span,
      );
    }
    if (!timers.has(transition)) {
      throw resolutionError(
        "PN201",
        `transition [${transition}] has timer maturity but no timer fact`,
        fact.span,
        `add \`[${transition}] timer clock (...) cel "..."\``,
      );
    }
  }
  for (const [transition, binds] of timerBinds) {
    const firstSpan = binds.values().next().value?.span as IrSpan;
    if (!transitions.includes(transition)) {
      throw resolutionError("PN202", `timer bind refers to unknown transition [${transition}]`, firstSpan);
    }
    if (!timers.has(transition)) {
      throw resolutionError(
        "PN201",
        `transition [${transition}] has timer binds but no timer fact`,
        firstSpan,
        `add \`[${transition}] timer clock (...) cel "..."\``,
      );
    }
  }
  for (const [transition, fact] of priorities) {
    if (!transitions.includes(transition)) {
      throw resolutionError("PN202", `priority refers to unknown transition [${transition}]`, fact.span);
    }
  }
  for (const [transition, fact] of handlers) {
    if (!transitions.includes(transition)) {
      throw resolutionError(
        "PN202",
        `handler refers to unknown transition [${transition}]`,
        fact.span,
      );
    }
  }

  const validateMetadataTarget = (
    targetType: string,
    targetName: string,
    span: IrSpan,
    noun: "description" | "annotation",
  ): void => {
    if (targetType === "place" && !accepts.has(targetName)) {
      throw resolutionError(
        "PN202",
        `${noun} references unknown place ${pythonRepr(targetName)}; metadata facts cannot declare semantic objects`,
        span,
        `declare the place in topology before ${noun === "description" ? "describing" : "annotating"} it`,
      );
    }
    if (targetType === "transition" && !transitions.includes(targetName)) {
      throw resolutionError(
        "PN202",
        `${noun} references unknown transition ${pythonRepr(targetName)}; metadata facts cannot declare semantic objects`,
        span,
        `declare the transition in topology before ${noun === "description" ? "describing" : "annotating"} it`,
      );
    }
    if (targetType === "arcHandle" && !arcHandles.has(targetName)) {
      throw resolutionError(
        "PN202",
        `${noun} references unknown arc handle @${targetName}`,
        span,
        `declare and name the arc before ${noun === "description" ? "describing" : "annotating"} it`,
      );
    }
  };
  for (const [key, fact] of descriptions) {
    const [type, name] = JSON.parse(key) as [string, string];
    validateMetadataTarget(type, name, fact.span, "description");
  }
  for (const [key, facts] of annotations) {
    const [type, name] = JSON.parse(key) as [string, string];
    const fact = facts.values().next().value as Fact<JSONValue>;
    validateMetadataTarget(type, name, fact.span, "annotation");
  }

  for (const [key, viewPositions] of positions) {
    const [viewName, subjectType] = JSON.parse(key) as [string, "place" | "transition"];
    for (const [subjectKey, fact] of viewPositions) {
      const subjectName = subjectKey.slice(subjectKey.indexOf(":") + 1);
      const known = subjectType === "place" ? accepts.has(subjectName) : transitions.includes(subjectName);
      if (!known) {
        const renderedViewName = TIMER_BIND_IDENTIFIER.test(viewName)
          ? viewName
          : JSON.stringify(viewName);
        const renderedSubjectName = TIMER_BIND_IDENTIFIER.test(subjectName)
          ? subjectName
          : JSON.stringify(subjectName);
        throw resolutionError(
          "PN202",
          `view ${pythonRepr(viewName)} references unknown ${subjectType} ${pythonRepr(subjectName)}; presentation facts cannot declare semantic objects`,
          relativeSpan(
            fact.span,
            `view ${renderedViewName} position `,
            `${subjectType === "place" ? "(" : "["}${renderedSubjectName}${subjectType === "place" ? ")" : "]"}`,
          ),
          `declare the ${subjectType} in topology before positioning it`,
        );
      }
    }
  }
  for (const [key, fact] of routes) {
    const [viewName, handle] = JSON.parse(key) as [string, string];
    if (!arcHandles.has(handle)) {
      throw resolutionError(
        "PN202",
        `view ${pythonRepr(viewName)} route references unknown arc handle @${handle}`,
        fact.span,
        "declare and name the routed arc before routing it",
      );
    }
  }

  if (header === undefined) header = {name: "unnamed"};
  if (transitionOrders.size > 0) {
    if (
      transitionOrders.size !== transitions.length ||
      transitions.some((name) => !transitionOrders.has(name)) ||
      new Set(transitionOrders.values()).size !== transitions.length ||
      [...transitionOrders.values()].some((rank) => rank < 1 || rank > transitions.length)
    ) {
      throw resolutionError("PN202", "transition order must rank every transition exactly once");
    }
    transitions.sort((left, right) => (transitionOrders.get(left) as number) - (transitionOrders.get(right) as number));
  }
  if (handleOrders.size > 0) {
    if (
      handleOrders.size !== arcHandles.size ||
      [...arcHandles.keys()].some((name) => !handleOrders.has(name)) ||
      new Set(handleOrders.values()).size !== arcHandles.size ||
      [...handleOrders.values()].some((rank) => rank < 1 || rank > arcHandles.size)
    ) {
      throw resolutionError("PN202", "arc-run order must rank every handle exactly once");
    }
    const covered = new Set([...arcHandles.values()].flat().map(identityKey));
    if ([...arcIndexes.keys()].some((id) => !covered.has(id)) || covered.size !== arcIndexes.size) {
      throw resolutionError("PN202", "arc-run order cannot omit arcs from an unhandled chain");
    }
    const orderedArcs: ResolvedArc[] = [];
    const orderedHandles = [...handleOrders.keys()].sort(
      (left, right) => (handleOrders.get(left) as number) - (handleOrders.get(right) as number),
    );
    for (const handle of orderedHandles) {
      for (const id of arcHandles.get(handle) as Identity[]) {
        const index = arcIndexes.get(identityKey(id));
        if (index === undefined) {
          throw resolutionError("PN202", `handle @${handle} refers to an unknown arc`);
        }
        orderedArcs.push(arcs[index] as ResolvedArc);
      }
    }
    arcs = orderedArcs;
  }

  let materializedTokenCount = 0;
  const reserveMaterializedTokens = (count: number): void => {
    const actual = materializedTokenCount + count;
    if (actual > PETRINET_RESOURCE_LIMITS.materializedTokens) {
      throw new PetrinetResourceError(
        "materializedTokens",
        PETRINET_RESOURCE_LIMITS.materializedTokens,
        actual,
      );
    }
    materializedTokenCount = actual;
  };

  const initialMarking: AnyObject = {};
  for (const fact of markings) {
    const accepted = accepts.get(fact.place);
    if (accepted === undefined) {
      throw resolutionError("PN202", `marking refers to unknown place (${fact.place})`, fact.span);
    }
    reserveMaterializedTokens(fact.count);
    let existing = Object.hasOwn(initialMarking, fact.place)
      ? initialMarking[fact.place] as AnyObject[]
      : undefined;
    if (existing === undefined) {
      existing = [];
      setOwn(initialMarking, fact.place, existing);
    }
    existing.push(...materializeMarkingToken({
      place: fact.place,
      token: fact.token,
      count: fact.count,
      acceptedColors: accepted,
      templates,
      span: fact.span,
    }));
  }

  const resolvedNamedMarkings: AnyObject = {};
  for (const [markingName, entries] of namedMarkings) {
    const resolvedMarking: AnyObject = {};
    for (const fact of entries) {
      const accepted = accepts.get(fact.place);
      if (accepted === undefined) {
        throw resolutionError(
          "PN202",
          `named marking ${pythonRepr(markingName)} references unknown place ${pythonRepr(fact.place)}; marking facts cannot declare semantic objects`,
          fact.span,
          "declare the place in topology before marking it",
        );
      }
      reserveMaterializedTokens(fact.count);
      let existing = Object.hasOwn(resolvedMarking, fact.place)
        ? resolvedMarking[fact.place] as AnyObject[]
        : undefined;
      if (existing === undefined) {
        existing = [];
        setOwn(resolvedMarking, fact.place, existing);
      }
      existing.push(...materializeMarkingToken({
        place: fact.place,
        token: fact.token,
        count: fact.count,
        acceptedColors: accepted,
        templates,
        span: fact.span,
        markingName,
      }));
    }
    setOwn(resolvedNamedMarkings, markingName, resolvedMarking);
  }

  const resolvedPlaces: AnyObject[] = [];
  for (const place of places) {
    const resolvedPlace: AnyObject = {name: place, accepts: accepts.get(place)};
    const capacity = capacities.get(place);
    if (capacity !== undefined) resolvedPlace.capacityPerColorKey = capacity.value;
    const port = ports.get(place);
    if (port !== undefined) resolvedPlace.port = port.value;
    const description = descriptions.get(metadataKey("place", place));
    if (description !== undefined) resolvedPlace.description = description.value;
    const annotationFacts = annotations.get(metadataKey("place", place));
    if (annotationFacts !== undefined && annotationFacts.size > 0) {
      resolvedPlace.annotations = Object.fromEntries(
        [...annotationFacts].map(([key, fact]) => [key, fact.value]),
      );
    }
    resolvedPlaces.push(resolvedPlace);
  }

  const resolvedTransitions: AnyObject[] = [];
  for (const transition of transitions) {
    const resolvedTransition: AnyObject = {name: transition};
    const handler = handlers.get(transition);
    if (handler !== undefined) resolvedTransition.handler = handler.value;
    const guard = guards.get(transition);
    if (guard !== undefined) resolvedTransition.guard = guard.value;
    const timerFact = timers.get(transition);
    if (timerFact !== undefined) {
      const timer: AnyObject = {clock: timerFact.clock, cel: timerFact.cel};
      const binds = timerBinds.get(transition);
      if (binds !== undefined && binds.size > 0) {
        timer.bind = Object.fromEntries(
          [...binds.keys()].sort().map((name) => [name, (binds.get(name) as Fact<string>).value]),
        );
      }
      const maturity = timerMaturities.get(transition);
      if (maturity !== undefined) timer.maturity = maturity.value;
      resolvedTransition.timer = timer;
    }
    const priority = priorities.get(transition);
    if (priority !== undefined && priority.value !== 0) resolvedTransition.priority = priority.value;
    const description = descriptions.get(metadataKey("transition", transition));
    if (description !== undefined) resolvedTransition.description = description.value;
    const annotationFacts = annotations.get(metadataKey("transition", transition));
    if (annotationFacts !== undefined && annotationFacts.size > 0) {
      resolvedTransition.annotations = Object.fromEntries(
        [...annotationFacts].map(([key, fact]) => [key, fact.value]),
      );
    }
    resolvedTransitions.push(resolvedTransition);
  }

  const currentArcIndexes = new Map<ResolvedArc, number>();
  arcs.forEach((arc, index) => currentArcIndexes.set(arc, index));
  for (const [handle, ids] of arcHandles) {
    const description = descriptions.get(metadataKey("arcHandle", handle));
    const annotationFacts = annotations.get(metadataKey("arcHandle", handle));
    if (description === undefined && (annotationFacts === undefined || annotationFacts.size === 0)) continue;
    for (const id of ids) {
      const arc = arcObjects.get(identityKey(id)) as ResolvedArc;
      const position = currentArcIndexes.get(arc) as number;
      if (description !== undefined) arcs[position]!.description = description.value;
      if (annotationFacts !== undefined && annotationFacts.size > 0) {
        arcs[position]!.annotations = Object.fromEntries(
          [...annotationFacts].map(([key, fact]) => [key, fact.value]),
        );
      }
    }
  }

  const result: AnyObject = {
    name: header.name,
    places: resolvedPlaces,
    transitions: resolvedTransitions,
    arcs,
  };
  if (
    markings.length > 0 ||
    (namedMarkings.size === 0 && positions.size === 0 && routes.size === 0 && extensions === undefined)
  ) {
    result.initialMarking = initialMarking;
  }

  const headerDescription = header.description;
  const metadataDescription = descriptions.get(metadataKey("document", ""));
  if (metadataDescription !== undefined) {
    if (headerDescription !== undefined && headerDescription !== metadataDescription.value) {
      throw resolutionError(
        "PN202",
        "conflicting description facts for net",
        metadataDescription.span,
      );
    }
    result.description = metadataDescription.value;
  } else if (headerDescription !== undefined) {
    result.description = headerDescription;
  }

  const documentAnnotations = annotations.get(metadataKey("document", ""));
  if (documentAnnotations !== undefined && documentAnnotations.size > 0) {
    result.annotations = Object.fromEntries(
      [...documentAnnotations].map(([key, fact]) => [key, fact.value]),
    );
  }

  const metadataHandles = new Set<string>();
  for (const key of [...descriptions.keys(), ...annotations.keys()]) {
    const [type, name] = JSON.parse(key) as [string, string];
    if (type === "arcHandle") metadataHandles.add(name);
  }
  for (const key of routes.keys()) {
    const [, handle] = JSON.parse(key) as [string, string];
    metadataHandles.add(handle);
  }

  const views: AnyObject = {};
  for (const [key, viewPositions] of positions) {
    const [viewName] = JSON.parse(key) as [string, string];
    let view = Object.hasOwn(views, viewName) ? views[viewName] as AnyObject : undefined;
    if (view === undefined) {
      view = {positions: {}, routes: {}};
      setOwn(views, viewName, view);
    }
    for (const [subject, fact] of viewPositions) {
      setOwn(view.positions, subject, fact.value);
    }
  }
  for (const [key, fact] of routes) {
    const [viewName, handle] = JSON.parse(key) as [string, string];
    let view = Object.hasOwn(views, viewName) ? views[viewName] as AnyObject : undefined;
    if (view === undefined) {
      view = {positions: {}, routes: {}};
      setOwn(views, viewName, view);
    }
    setOwn(view.routes, handle, {style: "orthogonal", points: fact.value});
  }

  if (
    Object.keys(resolvedNamedMarkings).length > 0 ||
    Object.keys(views).length > 0 ||
    extensions !== undefined ||
    metadataHandles.size > 0
  ) {
    const payloadHandles: AnyObject = {};
    for (const handle of [...metadataHandles].sort()) {
      const ids = arcHandles.get(handle) as Identity[];
      if (ids.length !== 1) {
        throw resolutionError(
          "PN202",
          `metadata arc handle '@${handle}' must identify exactly one arc`,
          arcHandleSpans.get(handle),
        );
      }
      const arcObject = arcObjects.get(identityKey(ids[0] as Identity)) as ResolvedArc;
      const currentIndex = currentArcIndexes.get(arcObject) as number;
      const resolvedArc = arcs[currentIndex] as AnyObject;
      const fingerprint = resolvedArc.consume !== undefined
        ? {
            from: cloneJson(resolvedArc.from),
            to: cloneJson(resolvedArc.to),
            type: resolvedArc.consume.type,
            mode: resolvedArc.consume.mode ?? "consume",
          }
        : {
            from: cloneJson(resolvedArc.from),
            to: cloneJson(resolvedArc.to),
            type: resolvedArc.produce.type,
            mode: "produce",
          };
      setOwn(payloadHandles, handle, {index: currentIndex, fingerprint});
    }
    const payload = {
      arcHandles: payloadHandles,
      markings: resolvedNamedMarkings,
      views,
      extensions: extensions?.value ?? {},
    };
    (result.annotations ??= {})["petrinet.dsl/v1"] = payload;
  }

  for (const [transition, timer] of timers) {
    if (!accepts.has(timer.clock)) {
      throw resolutionError(
        "PN201",
        `timer clock place (${timer.clock}) for transition [${transition}] is not declared`,
        timer.span,
        "declare the clock place in topology before using it in a timer",
      );
    }
    const binds = timerBinds.get(transition);
    if (binds !== undefined) {
      const bindingSources = new Set<string>();
      for (const arc of arcs) {
        if (
          arc.to?.transition === transition &&
          arc.consume !== undefined &&
          arc.consume.mode !== "inhibit"
        ) {
          bindingSources.add(arc.from.place);
        }
      }
      for (const [bindName, bind] of binds) {
        if (bindName === "clock") {
          throw resolutionError(
            "PN202",
            `timer bind name 'clock' is reserved on transition [${transition}]`,
            bind.span,
            "choose a bind name other than 'clock'",
          );
        }
        if (!bindingSources.has(bind.value)) {
          throw resolutionError(
            "PN202",
            `timer bind ${pythonRepr(bindName)} on transition [${transition}] names place (${bind.value}), but that place does not feed [${transition}] through a consume or read arc`,
            bind.span,
            "bind the variable to a consume or read input place",
          );
        }
      }
    }
    const maturity = timerMaturities.get(transition);
    if (
      !isValidCel(timer.cel) ||
      (maturity !== undefined && !isValidCel(maturity.value))
    ) {
      throw resolutionError(
        "PN203",
        `invalid CEL timer for transition [${transition}]`,
        timer.span,
        "fix the CEL expression syntax",
      );
    }
  }

  return result as JSONObject;
}
