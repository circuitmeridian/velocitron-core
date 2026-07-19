export interface SourcePosition {
  /** Zero-based UTF-8 byte offset. */
  readonly offset: number;
  /** One-based line number. */
  readonly line: number;
  /** One-based Unicode-scalar column number. */
  readonly column: number;
}

/** A half-open source span. */
export interface SourceSpan {
  readonly source: string;
  readonly start: SourcePosition;
  readonly end: SourcePosition;
}

export interface RelatedDiagnostic {
  readonly message: string;
  readonly span: SourceSpan;
}

export interface Diagnostic {
  readonly code: string;
  readonly message: string;
  readonly span: SourceSpan;
  readonly help?: string;
  readonly related?: readonly RelatedDiagnostic[];
}

export function renderDiagnostic(diagnostic: Diagnostic): string {
  const {source, start} = diagnostic.span;
  const primary =
    `${source}:${start.line}:${start.column}: error[${diagnostic.code}]: ${diagnostic.message}`;
  return diagnostic.help === undefined ? primary : `${primary}\nhelp: ${diagnostic.help}`;
}

function freezePosition(position: SourcePosition): SourcePosition {
  return Object.freeze({...position});
}

function freezeSpan(span: SourceSpan): SourceSpan {
  return Object.freeze({
    source: span.source,
    start: freezePosition(span.start),
    end: freezePosition(span.end),
  });
}

function freezeDiagnostic(diagnostic: Diagnostic): Diagnostic {
  const related = diagnostic.related === undefined
    ? undefined
    : Object.freeze(
      diagnostic.related.map((item) => Object.freeze({
        message: item.message,
        span: freezeSpan(item.span),
      })),
    );
  return Object.freeze({
    code: diagnostic.code,
    message: diagnostic.message,
    span: freezeSpan(diagnostic.span),
    ...(diagnostic.help === undefined ? {} : {help: diagnostic.help}),
    ...(related === undefined ? {} : {related}),
  });
}

export class PetrinetDslError extends Error {
  readonly diagnostic: Diagnostic;

  constructor(diagnostic: Diagnostic) {
    super(renderDiagnostic(diagnostic));
    this.name = "PetrinetDslError";
    this.diagnostic = freezeDiagnostic(diagnostic);
  }
}

/** Hard, deterministic compiler budgets checked before unbounded allocation. */
export const PETRINET_RESOURCE_LIMITS = Object.freeze({
  sourceBytes: 1_048_576,
  lexerTokens: 50_000,
  contributions: 10_000,
  irNodes: 10_000,
  materializedTokens: 10_000,
  diagnostics: 100,
  nestingDepth: 64,
} as const);

export type PetrinetResourceKind = keyof typeof PETRINET_RESOURCE_LIMITS;

const RESOURCE_LABELS: Readonly<Record<PetrinetResourceKind, string>> = {
  sourceBytes: "source UTF-8 bytes",
  lexerTokens: "lexer tokens",
  contributions: "Contributions",
  irNodes: "Contribution IR nodes",
  materializedTokens: "materialized marking tokens",
  diagnostics: "diagnostics",
  nestingDepth: "nesting depth",
};

/**
 * An operational resource failure, deliberately distinct from a PN diagnostic.
 */
export class PetrinetResourceError extends Error {
  readonly operation = "compile" as const;
  readonly resource: PetrinetResourceKind;
  readonly limit: number;
  readonly actual?: number;

  constructor(
    resource: PetrinetResourceKind,
    limit: number = PETRINET_RESOURCE_LIMITS[resource],
    actual?: number,
  ) {
    const observed = actual === undefined ? "" : `; observed ${actual}`;
    super(`Petri-net DSL ${RESOURCE_LABELS[resource]} limit exceeded: limit ${limit}${observed}.`);
    this.name = "PetrinetResourceError";
    this.resource = resource;
    this.limit = limit;
    if (actual !== undefined) this.actual = actual;
  }
}
