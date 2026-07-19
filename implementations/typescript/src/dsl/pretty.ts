import type {JSONValue} from "./types.js";

function containsOnlyUnicodeScalars(value: string): boolean {
  for (let index = 0; index < value.length; index += 1) {
    const unit = value.charCodeAt(index);
    if (unit >= 0xd800 && unit <= 0xdbff) {
      const next = value.charCodeAt(index + 1);
      if (!(next >= 0xdc00 && next <= 0xdfff)) return false;
      index += 1;
    } else if (unit >= 0xdc00 && unit <= 0xdfff) {
      return false;
    }
  }
  return true;
}

/** RFC 8785 lexicographic ordering over raw UTF-16 code units. */
function compareUtf16(left: string, right: string): number {
  const length = Math.min(left.length, right.length);
  for (let index = 0; index < length; index += 1) {
    const difference = left.charCodeAt(index) - right.charCodeAt(index);
    if (difference !== 0) return difference;
  }
  return left.length - right.length;
}

function renderString(value: string, objectKey: boolean): string {
  if (!containsOnlyUnicodeScalars(value)) {
    throw new RangeError(
      objectKey
        ? "JSON object keys must contain only Unicode scalars"
        : "JSON strings must contain only Unicode scalars",
    );
  }
  // JSON.stringify has the RFC 8785/ECMAScript string escaping required here.
  return JSON.stringify(value);
}

function renderNumber(value: number): string {
  if (!Number.isFinite(value)) throw new RangeError("JSON numbers must be finite");
  if (Object.is(value, -0)) return "0";

  // Number#toString is ECMAScript's shortest round-trippable binary64 form.
  // RFC 8785 omits the otherwise permitted plus sign in a positive exponent.
  return value.toString().replace("e+", "e");
}

function valueType(value: unknown): string {
  if (value === null) return "null";
  if (Array.isArray(value)) return "array";
  return typeof value;
}

/**
 * Renders the normative canonical JSON profile: two-space indentation, LF line
 * endings, a final LF, UTF-16-sorted object keys, and semantic array order.
 */
export function renderCanonicalJson(value: JSONValue): string {
  const active = new WeakSet<object>();

  const render = (item: unknown, level: number): string => {
    if (item === null) return "null";
    if (item === true) return "true";
    if (item === false) return "false";
    if (typeof item === "string") return renderString(item, false);
    if (typeof item === "number") return renderNumber(item);

    if (Array.isArray(item)) {
      if (active.has(item)) throw new TypeError("cyclic JSON value");
      active.add(item);
      try {
        if (item.length === 0) return "[]";
        const padding = "  ".repeat(level + 1);
        const closing = "  ".repeat(level);
        const values = Array.from(item, (child) => render(child, level + 1));
        return `[\n${padding}${values.join(`,\n${padding}`)}\n${closing}]`;
      } finally {
        active.delete(item);
      }
    }

    if (typeof item === "object" && item !== null) {
      const prototype = Object.getPrototypeOf(item);
      if (prototype !== Object.prototype && prototype !== null) {
        throw new TypeError(`not a JSON value: ${valueType(item)}`);
      }
      if (active.has(item)) throw new TypeError("cyclic JSON value");
      active.add(item);
      try {
        const symbolKeys = Object.getOwnPropertySymbols(item);
        if (symbolKeys.length > 0) throw new TypeError("JSON object keys must be strings");

        const object = item as Readonly<Record<string, unknown>>;
        const keys = Object.keys(object);
        for (const key of keys) {
          if (!containsOnlyUnicodeScalars(key)) {
            throw new RangeError("JSON object keys must contain only Unicode scalars");
          }
        }
        keys.sort(compareUtf16);
        if (keys.length === 0) return "{}";

        const padding = "  ".repeat(level + 1);
        const closing = "  ".repeat(level);
        const members = keys.map(
          (key) => `${renderString(key, true)}: ${render(object[key], level + 1)}`,
        );
        return `{\n${padding}${members.join(`,\n${padding}`)}\n${closing}}`;
      } finally {
        active.delete(item);
      }
    }

    throw new TypeError(`not a JSON value: ${valueType(item)}`);
  };

  return `${render(value, 0)}\n`;
}
