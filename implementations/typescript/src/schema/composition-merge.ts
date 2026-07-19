import {parseNet} from "./parse.js";
import type {
  Arc,
  CompositionDocument,
  CompositionWire,
  JsonValue,
  Net,
  Place,
  ReadonlyJsonObject,
  Timer,
  Token,
  Transition,
} from "./types.js";

/**
 * The runtime composition merge engine — a browser-safe port of the Python
 * reference implementation (`implementations/python/src/velocitron/composition.py`;
 * spec `spec/composition.md`; ADR 0004).
 *
 * Produces the combined {@link Net} from nets keyed by alias plus a list of
 * wires: alias-qualifies every place/transition/arc endpoint, fuses wired
 * port-places into single shared places (place-fusion realization), rewrites
 * arc endpoints and produce destinations, and re-exposes unwired ports as the
 * composition's own boundary ports. The output is one {@link Net}, verifiable
 * as a single Petri net and runnable by the `Engine`.
 *
 * Pure data transform — no fs/path/process; safe under the browser seam audit.
 */

/** Minimal union-find over port-place qualified names (N-ary fusion). */
class UnionFind {
  private readonly parent: Map<string, string>;

  constructor(names: Iterable<string>) {
    this.parent = new Map();
    for (const name of names) this.parent.set(name, name);
  }

  find(name: string): string {
    let root = name;
    while (this.parent.get(root) !== root) root = this.parent.get(root) as string;
    // Path compression.
    let current = name;
    while (this.parent.get(current) !== root) {
      const next = this.parent.get(current) as string;
      this.parent.set(current, root);
      current = next;
    }
    return root;
  }

  union(a: string, b: string): void {
    const ra = this.find(a);
    const rb = this.find(b);
    if (ra !== rb) this.parent.set(ra, rb);
  }
}

/**
 * Apply `rename` to a timer's place references (clock + bind values). The CEL
 * string is never touched — it references only the reserved `clock` variable
 * and the `bind` aliases, which is what makes a timed transition
 * composition-safe (ADR 0018): the merge rewrites the place *values* exactly as
 * it rewrites arc endpoints, and the expression survives.
 */
function mapTimerPlaces(timer: Timer, rename: (place: string) => string): Timer {
  return {
    clock: rename(timer.clock),
    cel: timer.cel,
    ...(timer.bind === undefined
      ? {}
      : {
          bind: Object.fromEntries(
            Object.entries(timer.bind).map(([variable, place]) => [
              variable,
              rename(place),
            ]),
          ),
        }),
    ...(timer.maturity === undefined ? {} : {maturity: timer.maturity}),
  };
}

/**
 * Annotations for a fused place (spec/composition.md "Fused-place annotations").
 *
 * Member ports' annotations merge output (source) ports before input ports —
 * the fused place is named after its sources, and their annotations take the
 * same precedence — each group in sorted qualified-name order, the earliest
 * member winning conflicting keys. The `fusion: true` tag is set last,
 * overriding any member value, so the viz renderer's fusion-place styling
 * triggers on every fused place.
 */
function fusedAnnotations(
  members: readonly string[],
  qnameToPlace: ReadonlyMap<string, Place>,
): ReadonlyJsonObject {
  const isOutput = (name: string): boolean => {
    const port = (qnameToPlace.get(name) as Place).port;
    return port !== undefined && port.direction === "output";
  };
  const ordered = [
    ...members.filter(isOutput).sort(),
    ...members.filter((member) => !isOutput(member)).sort(),
  ];
  const annotations: Record<string, JsonValue> = {};
  for (const member of ordered) {
    const memberAnnotations = (qnameToPlace.get(member) as Place).annotations;
    if (memberAnnotations === undefined) continue;
    for (const [key, value] of Object.entries(memberAnnotations)) {
      if (!(key in annotations)) annotations[key] = value;
    }
  }
  annotations.fusion = true;
  return annotations;
}

/**
 * Merge nets under aliases, fusing wired ports into shared places.
 *
 * Returns one {@link Net} that is structurally valid (it is re-parsed through
 * {@link parseNet}, which validates it and caches its compiled CEL programs) and
 * runnable by the `Engine`. Mirrors the Python `merge_nets` semantics exactly.
 */
export function mergeNets(
  aliasToNet: ReadonlyMap<string, Net>,
  wires: readonly CompositionWire[],
): Net {
  // ── 1. Qualify places and transitions; index port places by qualified name.
  const qualifiedPlaces: Place[] = [];
  const qnameToPlace = new Map<string, Place>();
  const qualifiedTransitions: Transition[] = [];
  for (const [alias, net] of aliasToNet) {
    for (const place of net.places) {
      const qname = `${alias}.${place.name}`;
      // description/annotations carry through qualification — doc-only
      // (ADR 0011), but consumers (e.g. the viz fusion-place styling) read them
      // off the merged net. Fused places (step 3) get their own annotations.
      const qualified: Place = {
        name: qname,
        accepts: [...place.accepts],
        ...(place.port === undefined ? {} : {port: place.port}),
        ...(place.description === undefined ? {} : {description: place.description}),
        ...(place.annotations === undefined ? {} : {annotations: place.annotations}),
      };
      qualifiedPlaces.push(qualified);
      qnameToPlace.set(qname, qualified);
    }
    for (const transition of net.transitions) {
      qualifiedTransitions.push({
        name: `${alias}.${transition.name}`,
        ...(transition.handler === undefined ? {} : {handler: transition.handler}),
        ...(transition.guard === undefined ? {} : {guard: transition.guard}),
        ...(transition.priority === undefined ? {} : {priority: transition.priority}),
        // Timer place references qualify like arc endpoints; the fusion rewrite
        // is applied in step 4, once the rewrite map exists. The CEL string is
        // never touched (ADR 0018).
        ...(transition.timer === undefined
          ? {}
          : {timer: mapTimerPlaces(transition.timer, (name) => `${alias}.${name}`)}),
        ...(transition.description === undefined
          ? {}
          : {description: transition.description}),
        ...(transition.annotations === undefined
          ? {}
          : {annotations: transition.annotations}),
      });
    }
  }

  // ── 2. Build port-fusion equivalence classes via union-find. A wire joins an
  // output port (from) to an input port (to); only wired ports participate, so
  // an unwired port stays a boundary (no class, no fusion).
  const wireEndpoints = wires.map(
    (wire) =>
      [`${wire.from.net}.${wire.from.port}`, `${wire.to.net}.${wire.to.port}`] as const,
  );
  const wired = new Set<string>();
  for (const [source, sink] of wireEndpoints) {
    wired.add(source);
    wired.add(sink);
  }
  const unionFind = new UnionFind(wired);
  for (const [source, sink] of wireEndpoints) unionFind.union(source, sink);

  const classes = new Map<string, string[]>();
  for (const name of [...wired].sort()) {
    const root = unionFind.find(name);
    const members = classes.get(root) ?? [];
    members.push(name);
    classes.set(root, members);
  }

  // rewriteMap: every member of a fused class → the fused place name. The fused
  // name is the sorted `__`-concatenation of the class's OUTPUT (source) port
  // qualified names — the place is named after what deposits into it. Input
  // ports are class members but never appear in the name. Deterministic
  // regardless of wire ordering; handles fan-in without a tiebreaker.
  const rewriteMap = new Map<string, string>();
  const fusedPlaces: Place[] = [];
  for (const members of classes.values()) {
    const sourcePorts = members
      .filter((member) => {
        const port = (qnameToPlace.get(member) as Place).port;
        return port !== undefined && port.direction === "output";
      })
      .sort();
    const fusedName = sourcePorts.join("__");
    const accepts: string[] = [];
    const seen = new Set<string>();
    for (const member of members) {
      for (const type of (qnameToPlace.get(member) as Place).accepts) {
        if (seen.has(type)) continue;
        seen.add(type);
        accepts.push(type);
      }
    }
    fusedPlaces.push({
      name: fusedName,
      accepts,
      annotations: fusedAnnotations(members, qnameToPlace),
    });
    for (const member of members) rewriteMap.set(member, fusedName);
  }

  // ── 3. Assemble places: unwired/non-port qualified places + fused places.
  const places: Place[] = qualifiedPlaces.filter(
    (place) => !rewriteMap.has(place.name),
  );
  places.push(...fusedPlaces);

  // ── 4. Rewrite arc endpoints and produce destinations to qualified/fused.
  const rewrite = (name: string): string => rewriteMap.get(name) ?? name;

  // Timer place references follow the same fusion rewrite as arc endpoints (a
  // wired clock port fuses into the shared place the timer must read).
  const transitions: Transition[] = qualifiedTransitions.map((transition) =>
    transition.timer === undefined
      ? transition
      : {
          ...transition,
          timer: mapTimerPlaces(transition.timer, (name) => rewrite(name)),
        },
  );

  const arcs: Arc[] = [];
  for (const [alias, net] of aliasToNet) {
    for (const arc of net.arcs) {
      if (arc.consume !== undefined) {
        arcs.push({
          from: {place: rewrite(`${alias}.${arc.from.place}`)},
          to: {transition: `${alias}.${arc.to.transition}`},
          consume: arc.consume,
          ...(arc.description === undefined ? {} : {description: arc.description}),
          ...(arc.annotations === undefined ? {} : {annotations: arc.annotations}),
        });
      } else {
        const produce = arc.produce;
        arcs.push({
          from: {transition: `${alias}.${arc.from.transition}`},
          to: {place: rewrite(`${alias}.${arc.to.place}`)},
          produce: {
            type: produce.type,
            destination: rewrite(`${alias}.${produce.destination}`),
            ...(produce.data === undefined ? {} : {data: produce.data}),
            ...(produce.cel === undefined ? {} : {cel: produce.cel}),
          },
          ...(arc.description === undefined ? {} : {description: arc.description}),
          ...(arc.annotations === undefined ? {} : {annotations: arc.annotations}),
        });
      }
    }
  }

  // ── 5. Compose initial markings: qualify keys, merge fused-place keys.
  const composed: Record<string, Token[]> = {};
  let hasMarking = false;
  for (const [alias, net] of aliasToNet) {
    if (net.initialMarking === undefined) continue;
    hasMarking = true;
    for (const [key, tokens] of Object.entries(net.initialMarking)) {
      const qualifiedKey = `${alias}.${key}`;
      const target = rewriteMap.get(qualifiedKey) ?? qualifiedKey;
      (composed[target] ??= []).push(...tokens);
    }
  }

  const result = {
    name: "composition",
    places,
    transitions,
    arcs,
    ...(hasMarking ? {initialMarking: composed} : {}),
  };

  // ── 6. Validate the composed net — "verifiable as one net", executable —
  // and cache its compiled CEL programs so the Engine can run it.
  return parseNet(result);
}

/**
 * Merge a composition document into a single {@link Net}, given its referenced
 * nets already resolved and keyed by their (default- or explicitly-)resolved
 * alias. Delegates to {@link mergeNets} using the document's wires. Unlike the
 * Python `merge_composition`, this never reads files: browser-side resolution
 * (see `resolve-composition.ts`) supplies `aliasToNet`.
 */
export function mergeComposition(
  composition: CompositionDocument,
  aliasToNet: ReadonlyMap<string, Net>,
): Net {
  return mergeNets(aliasToNet, composition.wires);
}
