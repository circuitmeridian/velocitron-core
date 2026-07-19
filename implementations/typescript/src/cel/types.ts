import type {ReadonlyJsonObject} from "../schema/types.js";

export interface CelSourceRange {
  readonly start: number;
  readonly end: number;
}

export type CelPhase = "compile" | "eval";

export interface CelDiagnostic {
  readonly phase: CelPhase;
  readonly code: string;
  readonly summary: string;
  readonly range?: CelSourceRange;
}

/** Browser-safe synchronous CEL boundary. Compiled values are intentionally opaque. */
export interface CelAdapter {
  compile(source: string): unknown;
  evaluate(compiled: unknown, context: ReadonlyJsonObject): unknown;
}
