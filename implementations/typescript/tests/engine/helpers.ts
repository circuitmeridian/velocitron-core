import {parseNet} from "../../src/schema/parse.js";
import type {Marking, Net, ReadonlyJsonObject, Token} from "../../src/schema/types.js";
import type {TransitionHandlerOutput} from "../../src/registry/types.js";

export function token(type: string, data: ReadonlyJsonObject = {}): Token {
  return {type, data};
}

export function completed(
  outputTokens: Readonly<Record<string, readonly Token[]>> = {},
  metadata: ReadonlyJsonObject = {},
): TransitionHandlerOutput {
  return {status: "completed", outputTokens, error: null, metadata};
}

export function failed(type = "Failure", message = "declared failure"): TransitionHandlerOutput {
  return {
    status: "failed",
    outputTokens: {},
    error: {type, message},
    metadata: {observed: true},
  };
}

export function parsedNet(document: unknown): Net {
  return parseNet(document);
}

export function nonempty(marking: Marking): Marking {
  return Object.fromEntries(Object.entries(marking).filter(([, tokens]) => tokens.length > 0));
}
