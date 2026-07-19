import type {CelAdapter} from "../cel/types.js";
import type {FiringRecord, InjectionRecord, Journal} from "../journal/types.js";
import type {Marking, Token} from "../schema/types.js";
import type {TokenBinding} from "../registry/types.js";

export type DepositViolationPolicy = "raise" | "record_then_raise" | "record_then_drop";

export interface EngineClock {
  now(): string;
}

export interface FiringIdInput {
  readonly netId: string;
  readonly transition: string;
  readonly attempt: number;
}

export interface InjectionIdInput {
  readonly netId: string;
  readonly place: string;
  readonly attempt: number;
}

export interface EngineIdFactory {
  firingId(input: FiringIdInput): string;
  injectionId(input: InjectionIdInput): string;
}

export interface EngineOptions {
  readonly celAdapter?: CelAdapter;
  readonly policy?: string;
  readonly journal?: Journal;
  readonly depositViolation?: DepositViolationPolicy;
  readonly maxConsecutiveFailures?: number | null;
  readonly clock?: EngineClock;
  readonly idFactory?: EngineIdFactory;
}

export interface AttemptOptions {
  readonly attempt: number;
}

export interface OptionalAttemptOptions {
  readonly attempt?: number;
}

export interface InjectOptions extends AttemptOptions {
  readonly replace?: boolean;
}

export interface RunOptions {
  readonly maxSteps?: number;
}

export interface TickOptions extends OptionalAttemptOptions, RunOptions {}

export interface FireResult {
  readonly marking: Marking;
  readonly record: FiringRecord;
}

export interface InjectionResult {
  readonly marking: Marking;
  readonly record: InjectionRecord;
}

export interface BatchInjectionResult {
  readonly marking: Marking;
  readonly records: readonly InjectionRecord[];
}

export interface TokenPlacement {
  readonly place: string;
  readonly token: Token;
}

export interface TimerMaturity {
  readonly transition: string;
  readonly clock: string;
  readonly at: number;
}

export type SelectedBinding = TokenBinding | null;
