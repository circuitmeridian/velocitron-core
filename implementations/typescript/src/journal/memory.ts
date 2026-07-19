import type {
  FiringRecord,
  InjectionRecord,
  Journal,
  JournalRecord,
  SequencedJournalRecord,
} from "./types.js";

/** Browser-safe journal with one monotonic stream across every record channel. */
export class InMemoryJournal implements Journal {
  readonly #records: SequencedJournalRecord[] = [];
  #sequence = 0;

  get records(): readonly SequencedJournalRecord[] {
    return this.#records;
  }

  #append(record: JournalRecord): void {
    this.#records.push(Object.freeze({...record, sequence: this.#sequence}));
    this.#sequence += 1;
  }

  recordFiring(record: FiringRecord): void {
    this.#append(record);
  }

  recordDepositViolation(record: FiringRecord): void {
    this.#append(record);
  }

  recordInjection(record: InjectionRecord): void {
    this.#append(record);
  }
}
