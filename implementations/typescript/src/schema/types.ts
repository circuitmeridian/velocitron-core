export type JsonPrimitive = null | boolean | number | string;

export type JsonValue =
  | JsonPrimitive
  | readonly JsonValue[]
  | ReadonlyJsonObject;

export interface ReadonlyJsonObject {
  readonly [key: string]: JsonValue;
}

export interface Token {
  readonly type: string;
  readonly data: ReadonlyJsonObject;
}

/** An immutable marking input suitable for Engine calls and initial markings. */
export type Marking = Readonly<Record<string, readonly Token[]>>;
export type MarkingInput = Readonly<
  Record<string, readonly Readonly<{type: string; data: ReadonlyJsonObject}>[]>
>;

export interface Port {
  readonly direction: "input" | "output";
  readonly type: string;
}

export interface CapacityPerColorKey {
  readonly key: string | readonly string[];
  readonly max: number;
}

export interface Place {
  readonly name: string;
  readonly accepts: readonly string[];
  readonly port?: Port;
  readonly capacityPerColorKey?: CapacityPerColorKey;
  readonly description?: string;
  readonly annotations?: ReadonlyJsonObject;
}

export interface Timer {
  readonly clock: string;
  readonly cel: string;
  readonly bind?: Readonly<Record<string, string>>;
  readonly maturity?: string;
}

export interface Transition {
  readonly name: string;
  readonly handler?: string;
  readonly guard?: string;
  readonly priority?: number;
  readonly timer?: Timer;
  readonly description?: string;
  readonly annotations?: ReadonlyJsonObject;
}

export type Predicate =
  | Readonly<{cel: string; handler?: never}>
  | Readonly<{handler: string; cel?: never}>;

export interface Correlate {
  readonly cel: string;
}

export interface ConsumePattern {
  readonly type: string;
  readonly mode: "consume" | "inhibit" | "read";
  readonly weight: number;
  readonly predicate?: Predicate;
  readonly correlate?: Correlate;
}

export interface ProduceTemplate {
  readonly type: string;
  readonly destination: string;
  readonly data?: ReadonlyJsonObject;
  readonly cel?: string;
}

export interface PlaceEndpoint {
  readonly place: string;
  readonly transition?: never;
}

export interface TransitionEndpoint {
  readonly transition: string;
  readonly place?: never;
}

export interface ConsumeArc {
  readonly from: PlaceEndpoint;
  readonly to: TransitionEndpoint;
  readonly consume: ConsumePattern;
  readonly produce?: never;
  readonly description?: string;
  readonly annotations?: ReadonlyJsonObject;
}

export interface ProduceArc {
  readonly from: TransitionEndpoint;
  readonly to: PlaceEndpoint;
  readonly produce: ProduceTemplate;
  readonly consume?: never;
  readonly description?: string;
  readonly annotations?: ReadonlyJsonObject;
}

export type Arc = ConsumeArc | ProduceArc;

export interface Net {
  readonly name: string;
  readonly places: readonly Place[];
  readonly transitions: readonly Transition[];
  readonly arcs: readonly Arc[];
  readonly initialMarking?: Marking;
  readonly description?: string;
  readonly annotations?: ReadonlyJsonObject;
}

export interface CompositionNetRef {
  readonly ref: string;
  readonly alias?: string;
}

export interface CompositionPortEndpoint {
  readonly net: string;
  readonly port: string;
}

export interface CompositionWire {
  readonly from: CompositionPortEndpoint;
  readonly to: CompositionPortEndpoint;
}

/** Shape-only composition document. References are deliberately unresolved. */
export interface CompositionDocument {
  readonly nets: readonly CompositionNetRef[];
  readonly wires: readonly CompositionWire[];
}
