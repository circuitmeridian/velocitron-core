import {lowerPetrinetText} from "./lower.js";
import {resolveContributionIr} from "./resolve.js";
import {deepFreezeJson} from "./types.js";
import type {JSONObject} from "./types.js";

export {
  PetrinetDslError,
  PetrinetResourceError,
  renderDiagnostic,
} from "./diagnostics.js";
export type {
  Diagnostic,
  RelatedDiagnostic,
  SourcePosition,
  SourceSpan,
} from "./diagnostics.js";

export interface CompiledNetDocument {
  readonly documentKind: "net";
  readonly document: Readonly<JSONObject>;
}

export interface CompiledCompositionDocument {
  readonly documentKind: "composition";
  readonly document: Readonly<JSONObject>;
}

export type CompiledPetrinetDocument =
  | CompiledNetDocument
  | CompiledCompositionDocument;

/**
 * Compile one complete `.petrinet` source into resolved canonical JSON.
 *
 * This deliberately stops before core-schema and semantic validation. Callers
 * branch on `documentKind`; standalone nets then pass through `parseNet`, while
 * compositions follow the composition loading stage.
 */
function normalizeSourceId(sourceId: string): string {
  if (/^(?:\/|[A-Za-z]:[\\/])/u.test(sourceId)) {
    const basename = sourceId.split(/[\\/]/u).filter(Boolean).at(-1);
    return basename ?? sourceId;
  }
  return sourceId;
}

export function compilePetrinetText(
  source: string,
  sourceId: string,
): CompiledPetrinetDocument {
  const contributionIr = lowerPetrinetText(source, normalizeSourceId(sourceId));
  const document = resolveContributionIr(contributionIr);
  return deepFreezeJson({
    documentKind: contributionIr.documentKind,
    document,
  }) as CompiledPetrinetDocument;
}
