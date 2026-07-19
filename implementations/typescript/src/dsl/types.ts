export type JSONPrimitive = null | boolean | number | string;

export type JSONValue = JSONPrimitive | JSONArray | JSONObject;

export interface JSONArray extends ReadonlyArray<JSONValue> {}

export interface JSONObject {
  readonly [key: string]: JSONValue;
}

/** UTF-8 source coordinates used by the portable Contribution IR wire format. */
export interface ContributionIrPosition {
  readonly byteOffset: number;
  readonly line: number;
  readonly column: number;
}

export interface ContributionIrSpan {
  readonly source: string;
  readonly start: ContributionIrPosition;
  readonly end: ContributionIrPosition;
}

export interface ContributionIrIdentity {
  readonly source: string;
  readonly statement: number;
  readonly part: number;
}

/**
 * Common closed contribution envelope. The generated schema validator narrows
 * each kind's target and value before the resolver examines them.
 */
export interface ContributionIrContribution {
  readonly id: ContributionIrIdentity;
  readonly ordinal: number;
  readonly span: ContributionIrSpan;
  readonly kind: string;
  readonly target: JSONObject;
  readonly value: JSONObject;
}

/** The root shape of velocitron.petrinet/contribution-ir version 1. */
export interface ContributionIr {
  readonly format: "velocitron.petrinet/contribution-ir";
  readonly version: 1;
  readonly documentKind: "net" | "composition";
  readonly document: Readonly<{id: string}>;
  readonly contributions: readonly ContributionIrContribution[];
}

/** Deeply freezes a JSON-shaped value without recursion or duplicate work. */
export function deepFreezeJson<T>(value: T): T {
  if (typeof value !== "object" || value === null) return value;

  const pending: object[] = [value];
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
