import type {Net} from "../schema/types.js";
import type {CelAdapter} from "./types.js";

export interface ParsedCelPrograms {
  readonly adapter: CelAdapter;
  readonly programs: ReadonlyMap<string, unknown>;
}

const parsedCelPrograms = new WeakMap<Net, ParsedCelPrograms>();

export function rememberParsedCelPrograms(
  net: Net,
  adapter: CelAdapter,
  programs: ReadonlyMap<string, unknown>,
): void {
  parsedCelPrograms.set(net, {adapter, programs: new Map(programs)});
}

/** Internal Engine seam; intentionally absent from the package root exports. */
export function parsedCelProgramsFor(net: Net): ParsedCelPrograms | undefined {
  return parsedCelPrograms.get(net);
}
