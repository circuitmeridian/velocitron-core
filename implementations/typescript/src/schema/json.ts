import type {JsonValue} from "./types.js";

export interface JsonShapeIssue {
  readonly code: string;
  readonly path: string;
  readonly message: string;
}

export interface JsonSnapshot {
  readonly value: JsonValue;
  readonly issues: readonly JsonShapeIssue[];
}

/** Hard budgets applied while taking an accessor-free JSON snapshot. */
export const JSON_SNAPSHOT_LIMITS = Object.freeze({
  nestingDepth: 64,
  containerEntries: 50_000,
  totalValues: 100_000,
} as const);

function childPath(path: string, key: string): string {
  return `${path}/${key.replaceAll("~", "~0").replaceAll("/", "~1")}`;
}

/** Copies only JSON-native data properties, without invoking accessors. */
export function snapshotJson(value: unknown): JsonSnapshot {
  const issues: JsonShapeIssue[] = [];
  const active = new WeakSet<object>();
  const completed = new WeakMap<object, JsonValue>();
  let visitedValues = 0;
  let halted = false;

  const resourceIssue = (code: string, path: string, message: string): void => {
    if (halted) return;
    issues.push({code, path, message});
    halted = true;
  };

  const totalValueIssue = (path: string): void => {
    resourceIssue(
      "json.max_total_values",
      path,
      `JSON total value count exceeds the limit of ${JSON_SNAPSHOT_LIMITS.totalValues}`,
    );
  };

  const beginValue = (path: string): boolean => {
    if (halted) return false;
    if (visitedValues >= JSON_SNAPSHOT_LIMITS.totalValues) {
      totalValueIssue(path);
      return false;
    }
    visitedValues++;
    return true;
  };

  const hasValueBudget = (path: string): boolean => {
    if (halted) return false;
    if (visitedValues >= JSON_SNAPSHOT_LIMITS.totalValues) {
      totalValueIssue(path);
      return false;
    }
    return true;
  };

  const hasDirectEntryBudget = (entryCount: number, path: string): boolean => {
    if (entryCount > JSON_SNAPSHOT_LIMITS.totalValues - visitedValues) {
      totalValueIssue(path);
      return false;
    }
    return true;
  };

  const unreadableObject = (path: string): void => {
    issues.push({
      code: "json.unreadable_object",
      path,
      message: "JSON objects must expose ordinary own data properties",
    });
  };

  const visit = (candidate: unknown, path: string, depth: number): JsonValue => {
    if (!beginValue(path)) return null;
    if (depth > JSON_SNAPSHOT_LIMITS.nestingDepth) {
      resourceIssue(
        "json.max_depth",
        path,
        `JSON nesting depth exceeds the limit of ${JSON_SNAPSHOT_LIMITS.nestingDepth}`,
      );
      return null;
    }
    if (
      candidate === null ||
      typeof candidate === "string" ||
      typeof candidate === "boolean"
    ) {
      return candidate;
    }
    if (typeof candidate === "number") {
      if (!Number.isFinite(candidate)) {
        issues.push({
          code: "json.non_finite_number",
          path,
          message: "JSON numbers must be finite",
        });
        return null;
      }
      if (Math.abs(candidate) > Number.MAX_SAFE_INTEGER) {
        issues.push({
          code: "json.unsafe_number",
          path,
          message: `JSON numbers must be between ${Number.MIN_SAFE_INTEGER} and ${Number.MAX_SAFE_INTEGER}`,
        });
        return null;
      }
      return candidate;
    }
    if (typeof candidate !== "object") {
      issues.push({
        code: "json.unsupported_type",
        path,
        message: `JSON values cannot contain ${typeof candidate}`,
      });
      return null;
    }

    if (active.has(candidate)) {
      issues.push({code: "json.cycle", path, message: "JSON values cannot contain cycles"});
      return null;
    }
    const prior = completed.get(candidate);
    if (prior !== undefined) return prior;

    let isArray: boolean;
    try {
      isArray = Array.isArray(candidate);
    } catch {
      unreadableObject(path);
      return null;
    }

    if (isArray) {
      let arrayLength: number;
      try {
        const lengthDescriptor = Object.getOwnPropertyDescriptor(candidate, "length");
        if (
          lengthDescriptor === undefined ||
          !("value" in lengthDescriptor) ||
          typeof lengthDescriptor.value !== "number"
        ) {
          unreadableObject(path);
          return null;
        }
        arrayLength = lengthDescriptor.value;
      } catch {
        unreadableObject(path);
        return null;
      }
      if (arrayLength > JSON_SNAPSHOT_LIMITS.containerEntries) {
        resourceIssue(
          "json.max_container_entries",
          path,
          `JSON container entry count exceeds the limit of ${JSON_SNAPSHOT_LIMITS.containerEntries}`,
        );
        return null;
      }

      let prototype: object | null;
      let ownKeys: readonly (string | symbol)[];
      try {
        prototype = Object.getPrototypeOf(candidate) as object | null;
        ownKeys = Reflect.ownKeys(candidate);
      } catch {
        unreadableObject(path);
        return null;
      }

      if (ownKeys.length - 1 > JSON_SNAPSHOT_LIMITS.containerEntries) {
        resourceIssue(
          "json.max_container_entries",
          path,
          `JSON container entry count exceeds the limit of ${JSON_SNAPSHOT_LIMITS.containerEntries}`,
        );
        return null;
      }

      const presentIndexes = new Set<string>();
      const extraKeys: Array<string | symbol> = [];
      for (const key of ownKeys) {
        if (key === "length") continue;
        if (typeof key === "string") {
          const index = Number(key);
          if (
            Number.isInteger(index) &&
            index >= 0 &&
            index < arrayLength &&
            String(index) === key
          ) {
            presentIndexes.add(key);
            continue;
          }
        }
        extraKeys.push(key);
      }
      const declaredEntries = arrayLength + extraKeys.length;
      if (declaredEntries > JSON_SNAPSHOT_LIMITS.containerEntries) {
        resourceIssue(
          "json.max_container_entries",
          path,
          `JSON container entry count exceeds the limit of ${JSON_SNAPSHOT_LIMITS.containerEntries}`,
        );
        return null;
      }
      if (!hasDirectEntryBudget(declaredEntries, path)) return null;

      if (prototype !== Array.prototype) {
        issues.push({
          code: "json.non_plain_array",
          path,
          message: "JSON arrays must use the built-in Array prototype",
        });
      }

      active.add(candidate);
      const result: JsonValue[] = [];
      let descriptorReadFailed = false;
      for (let index = 0; index < arrayLength; index++) {
        const key = String(index);
        const itemPath = childPath(path, key);
        if (!hasValueBudget(itemPath)) break;
        if (!presentIndexes.has(key)) {
          visitedValues++;
          issues.push({
            code: "json.sparse_array",
            path: itemPath,
            message: "JSON arrays cannot contain holes",
          });
          result.push(null);
          continue;
        }

        let descriptor: PropertyDescriptor | undefined;
        try {
          descriptor = Object.getOwnPropertyDescriptor(candidate, key);
        } catch {
          descriptorReadFailed = true;
          break;
        }
        if (descriptor === undefined || !("value" in descriptor) || !descriptor.enumerable) {
          visitedValues++;
          issues.push({
            code: "json.invalid_property",
            path: itemPath,
            message: "JSON members must be enumerable data properties",
          });
          result.push(null);
        } else {
          result.push(visit(descriptor.value, itemPath, depth + 1));
        }
        if (halted) break;
      }

      for (const key of extraKeys) {
        if (halted) break;
        const memberPath = typeof key === "symbol" ? path : childPath(path, key);
        if (!beginValue(memberPath)) break;
        if (typeof key === "symbol") {
          issues.push({
            code: "json.symbol_key",
            path,
            message: "JSON values cannot contain symbol keys",
          });
        } else {
          issues.push({
            code: "json.array_property",
            path: memberPath,
            message: "JSON arrays cannot contain named properties",
          });
        }
      }

      active.delete(candidate);
      if (descriptorReadFailed) {
        unreadableObject(path);
        return null;
      }
      if (!halted) completed.set(candidate, result);
      return result;
    }

    let prototype: object | null;
    try {
      prototype = Object.getPrototypeOf(candidate) as object | null;
    } catch {
      unreadableObject(path);
      return null;
    }
    if (prototype !== Object.prototype && prototype !== null) {
      issues.push({
        code: "json.non_plain_object",
        path,
        message: "JSON objects must use Object.prototype or a null prototype",
      });
      return null;
    }

    let ownKeys: readonly (string | symbol)[];
    try {
      ownKeys = Reflect.ownKeys(candidate);
    } catch {
      unreadableObject(path);
      return null;
    }
    if (ownKeys.length > JSON_SNAPSHOT_LIMITS.containerEntries) {
      resourceIssue(
        "json.max_container_entries",
        path,
        `JSON container entry count exceeds the limit of ${JSON_SNAPSHOT_LIMITS.containerEntries}`,
      );
      return null;
    }
    if (!hasDirectEntryBudget(ownKeys.length, path)) return null;

    active.add(candidate);
    const result = Object.create(null) as Record<string, JsonValue>;
    let descriptorReadFailed = false;
    for (const key of ownKeys) {
      if (halted) break;
      const memberPath = typeof key === "symbol" ? path : childPath(path, key);
      if (!hasValueBudget(memberPath)) break;

      let descriptor: PropertyDescriptor | undefined;
      try {
        descriptor = Object.getOwnPropertyDescriptor(candidate, key);
      } catch {
        descriptorReadFailed = true;
        break;
      }
      if (typeof key === "symbol") {
        visitedValues++;
        issues.push({
          code: "json.symbol_key",
          path,
          message: "JSON values cannot contain symbol keys",
        });
        continue;
      }
      if (descriptor === undefined || !("value" in descriptor) || !descriptor.enumerable) {
        visitedValues++;
        issues.push({
          code: "json.invalid_property",
          path: memberPath,
          message: "JSON members must be enumerable data properties",
        });
        continue;
      }
      Object.defineProperty(result, key, {
        configurable: true,
        enumerable: true,
        value: visit(descriptor.value, memberPath, depth + 1),
        writable: true,
      });
    }
    active.delete(candidate);
    if (descriptorReadFailed) {
      unreadableObject(path);
      return null;
    }
    if (!halted) completed.set(candidate, result);
    return result;
  };

  return {issues, value: visit(value, "", 0)};
}

export function deepFreezeJson<T>(value: T): T {
  const pending: object[] = typeof value === "object" && value !== null ? [value] : [];
  const seen = new WeakSet<object>();
  while (pending.length > 0) {
    const current = pending.pop();
    if (current === undefined || seen.has(current)) continue;
    seen.add(current);
    for (const child of Object.values(current)) {
      if (typeof child === "object" && child !== null) pending.push(child);
    }
    Object.freeze(current);
  }
  return value;
}
