import {
  Environment,
  EvaluationError as BackendEvaluationError,
  ParseError as BackendParseError,
  TypeError as BackendTypeError,
  type Context,
  type ParseResult,
} from "@marcbachmann/cel-js";
import {snapshotJson} from "../schema/json.js";
import type {ReadonlyJsonObject} from "../schema/types.js";
import {CelCompileError, CelEvaluationError, normalizeCelError} from "./errors.js";
import type {CelAdapter} from "./types.js";

function promoteJsonIntegers(value: unknown): unknown {
  if (typeof value === "number" && Number.isSafeInteger(value)) return BigInt(value);
  if (Array.isArray(value)) {
    for (let index = 0; index < value.length; index += 1) {
      value[index] = promoteJsonIntegers(value[index]);
    }
    return value;
  }
  if (typeof value === "object" && value !== null) {
    const record = value as Record<string, unknown>;
    for (const key of Object.keys(record)) record[key] = promoteJsonIntegers(record[key]);
  }
  return value;
}

function jsonResult(value: unknown, active = new WeakSet<object>()): unknown {
  if (
    value === null ||
    typeof value === "string" ||
    typeof value === "boolean"
  ) {
    return value;
  }
  if (typeof value === "number") {
    if (Number.isFinite(value) && (!Number.isInteger(value) || Number.isSafeInteger(value))) {
      return value;
    }
    throw new CelEvaluationError({
      code: "invalid_result",
      summary: "CEL produced a number that cannot cross a JSON boundary safely",
    });
  }
  if (typeof value === "bigint") {
    const number = Number(value);
    if (Number.isSafeInteger(number) && BigInt(number) === value) return number;
    throw new CelEvaluationError({
      code: "invalid_result",
      summary: "CEL produced an integer outside the JSON safe-integer range",
    });
  }
  if (typeof value !== "object") {
    throw new CelEvaluationError({
      code: "invalid_result",
      summary: `CEL produced a non-JSON ${typeof value} result`,
    });
  }
  if (active.has(value)) {
    throw new CelEvaluationError({
      code: "invalid_result",
      summary: "CEL produced a cyclic result",
    });
  }
  active.add(value);
  if (Array.isArray(value)) {
    const result = value.map((item) => jsonResult(item, active));
    active.delete(value);
    return result;
  }
  const prototype = Object.getPrototypeOf(value) as object | null;
  if (prototype !== Object.prototype && prototype !== null) {
    active.delete(value);
    throw new CelEvaluationError({
      code: "invalid_result",
      summary: "CEL produced a non-JSON object result",
    });
  }
  const result = Object.create(null) as Record<string, unknown>;
  for (const [key, item] of Object.entries(value)) result[key] = jsonResult(item, active);
  active.delete(value);
  return result;
}

/** Default synchronous browser adapter over @marcbachmann/cel-js 8. */
export class DefaultCelAdapter implements CelAdapter {
  readonly #environment = new Environment({
    limits: {maxAstNodes: 10_000, maxDepth: 100},
    unlistedVariablesAreDyn: true,
  });
  readonly #programsBySource = new Map<string, object>();
  readonly #runsByProgram = new WeakMap<object, ParseResult>();

  compile(source: string): unknown {
    const cached = this.#programsBySource.get(source);
    if (cached !== undefined) return cached;

    try {
      const run = this.#environment.parse(source);
      const program = Object.freeze(Object.create(null)) as object;
      this.#programsBySource.set(source, program);
      this.#runsByProgram.set(program, run);
      return program;
    } catch (error) {
      if (error instanceof BackendParseError) throw normalizeCelError(error, "compile");
      throw new CelCompileError({
        code: "backend_error",
        summary: error instanceof Error && error.message.length > 0
          ? error.message
          : "CEL compile failed",
      });
    }
  }

  evaluate(compiled: unknown, context: ReadonlyJsonObject): unknown {
    if (typeof compiled !== "object" || compiled === null) {
      throw new CelEvaluationError({
        code: "invalid_program",
        summary: "CEL program was not compiled by this adapter",
      });
    }
    const run = this.#runsByProgram.get(compiled);
    if (run === undefined) {
      throw new CelEvaluationError({
        code: "invalid_program",
        summary: "CEL program was not compiled by this adapter",
      });
    }

    const snapshot = snapshotJson(context);
    if (snapshot.issues.length > 0 || typeof snapshot.value !== "object" || snapshot.value === null || Array.isArray(snapshot.value)) {
      throw new CelEvaluationError({
        code: "invalid_context",
        summary: "CEL context must be a JSON object",
      });
    }

    try {
      const result: unknown = run(promoteJsonIntegers(snapshot.value) as Context);
      if (
        (typeof result === "object" || typeof result === "function") &&
        result !== null &&
        "then" in result &&
        typeof result.then === "function"
      ) {
        throw new CelEvaluationError({
          code: "async_result",
          summary: "CEL evaluation must be synchronous",
        });
      }
      return jsonResult(result);
    } catch (error) {
      if (error instanceof CelEvaluationError) throw error;
      if (error instanceof BackendEvaluationError || error instanceof BackendTypeError) {
        throw normalizeCelError(error, "eval");
      }
      throw new CelEvaluationError({
        code: "backend_error",
        summary: error instanceof Error && error.message.length > 0
          ? error.message
          : "CEL evaluation failed",
      });
    }
  }
}

export function createDefaultCelAdapter(): CelAdapter {
  return new DefaultCelAdapter();
}
