import type {ReadonlyJsonObject, Token} from "../schema/types.js";
import type {FiringTimestamps, HandlerError, OutputTokens, TokenBinding} from "../registry/types.js";

export type FiringStatus = "completed" | "failed";

export interface FiringRecord {
  readonly firingId: string;
  readonly netId: string;
  readonly transition: string;
  readonly attempt: number;
  readonly status: FiringStatus;
  readonly inputTokens: TokenBinding;
  readonly outputTokens: OutputTokens;
  readonly error: HandlerError | null;
  readonly metadata: ReadonlyJsonObject;
  readonly timestamps: FiringTimestamps;
}

export type InjectionKind = "inject" | "update";

export interface InjectionRecord {
  readonly injectionId: string;
  readonly netId: string;
  readonly place: string;
  readonly attempt: number;
  readonly kind: InjectionKind;
  readonly tokens: readonly Token[];
  readonly replaced: readonly Token[];
  readonly timestamps: FiringTimestamps;
}

export interface Journal {
  recordFiring(record: FiringRecord): void;
  recordDepositViolation(record: FiringRecord): void;
  recordInjection(record: InjectionRecord): void;
}

export type JournalRecord = FiringRecord | InjectionRecord;
export type SequencedJournalRecord = JournalRecord & Readonly<{sequence: number}>;
