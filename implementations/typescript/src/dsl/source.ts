import type {SourcePosition, SourceSpan} from "./diagnostics.js";

const UTF8_ENCODER = new TextEncoder();

/** Returns the browser's strict UTF-8 encoding size for a JavaScript string. */
export function utf8ByteLength(text: string): number {
  return UTF8_ENCODER.encode(text).byteLength;
}

function utf8ScalarLength(codePoint: number): number {
  if (codePoint <= 0x7f) return 1;
  if (codePoint <= 0x7ff) return 2;
  if (codePoint <= 0xffff) return 3;
  return 4;
}

/**
 * Maps ANTLR/JavaScript UTF-16 indices to portable source coordinates.
 *
 * Byte offsets count UTF-8 bytes. Lines and columns are one-based and columns
 * count Unicode scalar values, not UTF-16 code units. CRLF is retained exactly:
 * the CR occupies a column and the following LF starts the next line.
 */
export class SourceMap {
  readonly sourceId: string;
  readonly text: string;
  readonly byteLength: number;

  readonly #byteOffsets: Uint32Array;
  readonly #lines: Uint32Array;
  readonly #columns: Uint32Array;

  constructor(sourceId: string, text: string) {
    this.sourceId = sourceId;
    this.text = text;
    this.byteLength = utf8ByteLength(text);

    const size = text.length + 1;
    this.#byteOffsets = new Uint32Array(size);
    this.#lines = new Uint32Array(size);
    this.#columns = new Uint32Array(size);

    let byteOffset = 0;
    let line = 1;
    let column = 1;
    let index = 0;
    while (index < text.length) {
      this.#byteOffsets[index] = byteOffset;
      this.#lines[index] = line;
      this.#columns[index] = column;

      const first = text.charCodeAt(index);
      const second = index + 1 < text.length ? text.charCodeAt(index + 1) : -1;
      const isPair =
        first >= 0xd800 && first <= 0xdbff && second >= 0xdc00 && second <= 0xdfff;
      if (isPair) {
        // A boundary within a surrogate pair has no Unicode-scalar coordinate.
        // Map it to the scalar's start; parser token boundaries never split pairs.
        this.#byteOffsets[index + 1] = byteOffset;
        this.#lines[index + 1] = line;
        this.#columns[index + 1] = column;
        const codePoint = 0x10000 + ((first - 0xd800) << 10) + (second - 0xdc00);
        byteOffset += utf8ScalarLength(codePoint);
        column += 1;
        index += 2;
      } else {
        byteOffset += utf8ScalarLength(first);
        if (first === 0x0a) {
          line += 1;
          column = 1;
        } else {
          column += 1;
        }
        index += 1;
      }

      this.#byteOffsets[index] = byteOffset;
      this.#lines[index] = line;
      this.#columns[index] = column;
    }
  }

  position(index: number): SourcePosition {
    const bounded = Number.isFinite(index)
      ? Math.max(0, Math.min(Math.trunc(index), this.text.length))
      : index === Number.POSITIVE_INFINITY
        ? this.text.length
        : 0;
    return Object.freeze({
      offset: this.#byteOffsets[bounded] ?? this.byteLength,
      line: this.#lines[bounded] || 1,
      column: this.#columns[bounded] || 1,
    });
  }

  span(start: number, end: number): SourceSpan {
    return Object.freeze({
      source: this.sourceId,
      start: this.position(start),
      end: this.position(end),
    });
  }

  eofSpan(): SourceSpan {
    return this.span(this.text.length, this.text.length);
  }
}
