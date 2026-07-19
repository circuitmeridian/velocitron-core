import type {CelDiagnostic, CelPhase, CelSourceRange} from "./types.js";

function freezeDiagnostic(diagnostic: CelDiagnostic): CelDiagnostic {
  const range = diagnostic.range === undefined ? undefined : Object.freeze({...diagnostic.range});
  return Object.freeze({...diagnostic, ...(range === undefined ? {} : {range})});
}

export class CelError extends Error {
  readonly diagnostic: CelDiagnostic;

  constructor(diagnostic: CelDiagnostic) {
    super(diagnostic.summary);
    this.name = "CelError";
    this.diagnostic = freezeDiagnostic(diagnostic);
  }
}

export class CelCompileError extends CelError {
  constructor(diagnostic: Omit<CelDiagnostic, "phase">) {
    super({...diagnostic, phase: "compile"});
    this.name = "CelCompileError";
  }
}

export class CelEvaluationError extends CelError {
  constructor(diagnostic: Omit<CelDiagnostic, "phase">) {
    super({...diagnostic, phase: "eval"});
    this.name = "CelEvaluationError";
  }
}

interface BackendErrorShape {
  readonly code?: unknown;
  readonly summary?: unknown;
  readonly message?: unknown;
  readonly range?: unknown;
}

function normalizedRange(value: unknown): CelSourceRange | undefined {
  if (typeof value !== "object" || value === null) return undefined;
  const range = value as {readonly start?: unknown; readonly end?: unknown};
  if (
    typeof range.start !== "number" ||
    !Number.isSafeInteger(range.start) ||
    range.start < 0 ||
    typeof range.end !== "number" ||
    !Number.isSafeInteger(range.end) ||
    range.end < range.start
  ) {
    return undefined;
  }
  return {start: range.start, end: range.end};
}

export function normalizeCelError(error: unknown, phase: CelPhase): CelError {
  if (error instanceof CelError) {
    if (error.diagnostic.phase === phase) return error;
    const diagnostic = {
      code: error.diagnostic.code,
      summary: error.diagnostic.summary,
      ...(error.diagnostic.range === undefined ? {} : {range: error.diagnostic.range}),
    };
    return phase === "compile"
      ? new CelCompileError(diagnostic)
      : new CelEvaluationError(diagnostic);
  }

  const backend =
    typeof error === "object" && error !== null ? (error as BackendErrorShape) : undefined;
  const code = typeof backend?.code === "string" && backend.code.length > 0
    ? backend.code
    : "backend_error";
  const summaryCandidate = backend?.summary ?? backend?.message;
  const summary = typeof summaryCandidate === "string" && summaryCandidate.length > 0
    ? summaryCandidate
    : `CEL ${phase} failed`;
  const range = normalizedRange(backend?.range);
  const diagnostic = {...(range === undefined ? {} : {range}), code, summary};
  return phase === "compile"
    ? new CelCompileError(diagnostic)
    : new CelEvaluationError(diagnostic);
}
