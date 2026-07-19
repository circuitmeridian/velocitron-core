import {deepFreezeJson} from "./json.js";
import type {CompositionDocument} from "./types.js";
import {validateCompositionDocumentShape} from "./validate.js";

/** Validates only the canonical composition document shape; it never loads or merges refs. */
export function parseCompositionShape(input: unknown): CompositionDocument {
  const document = validateCompositionDocumentShape(input) as unknown as CompositionDocument;
  return deepFreezeJson(document);
}
