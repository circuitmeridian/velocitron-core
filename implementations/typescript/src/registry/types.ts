import type {JsonValue, Marking, ReadonlyJsonObject, Token} from "../schema/types.js";

export interface FiringTimestamps {
  readonly fired_at: string;
}

export interface FiringContext {
  readonly firingId: string;
  readonly attempt: number;
  readonly netId: string;
  readonly timestamps: FiringTimestamps;
}

export interface HandlerError {
  readonly type: string;
  readonly message: string;
}

export type TokenBinding = Readonly<Record<string, readonly Token[]>>;
export type OutputTokens = Readonly<Record<string, readonly Token[]>>;

export interface TransitionHandlerInput {
  readonly transitionId: string;
  readonly inputTokens: TokenBinding;
  readonly firingContext: FiringContext;
}

export interface TransitionHandlerOutput {
  readonly status: "completed" | "failed";
  readonly outputTokens: OutputTokens;
  readonly error: HandlerError | null;
  readonly metadata: ReadonlyJsonObject;
}

export type TransitionHandler = (input: TransitionHandlerInput) => TransitionHandlerOutput;
export type GuardHandler = (input: TransitionHandlerInput) => boolean;

export interface PredicateHandlerInput {
  readonly token: Token;
  readonly firingContext: FiringContext;
}

export type PredicateHandler = (input: PredicateHandlerInput) => boolean;

export interface FiringPolicyInput {
  readonly marking: Marking;
  readonly enabledTransitions: readonly string[];
  readonly priorities: Readonly<Record<string, number>>;
  readonly consecutiveFailures: Readonly<Record<string, number>>;
}

export type FiringPolicyHandler = (input: FiringPolicyInput) => string | null;

export type HandlerKind = "transition" | "guard" | "predicate" | "firing-policy";

/** JSON metadata is intentionally opaque to the Engine. */
export type HandlerMetadataValue = JsonValue;
