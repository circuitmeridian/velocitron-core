import {HandlerConflictError, HandlerNotFoundError, InvalidHandlerRegistrationError} from "./errors.js";
import type {
  FiringPolicyHandler,
  FiringPolicyInput,
  GuardHandler,
  HandlerKind,
  PredicateHandler,
  TransitionHandler,
} from "./types.js";

export const DEFAULT_FIRING_POLICY = "first-found";
export const PRIORITY_FIRING_POLICY = "priority";

function firstFound(input: FiringPolicyInput): string | null {
  return input.enabledTransitions[0] ?? null;
}

function priority(input: FiringPolicyInput): string | null {
  let selected: string | null = null;
  let selectedPriority = Number.NEGATIVE_INFINITY;
  for (const name of input.enabledTransitions) {
    const candidatePriority = input.priorities[name] ?? 0;
    if (selected === null || candidatePriority > selectedPriority) {
      selected = name;
      selectedPriority = candidatePriority;
    }
  }
  return selected;
}

type HandlerByKind = {
  readonly transition: TransitionHandler;
  readonly guard: GuardHandler;
  readonly predicate: PredicateHandler;
  readonly "firing-policy": FiringPolicyHandler;
};

export class HandlerRegistry {
  readonly #transitions = new Map<string, TransitionHandler>();
  readonly #guards = new Map<string, GuardHandler>();
  readonly #predicates = new Map<string, PredicateHandler>();
  readonly #policies = new Map<string, FiringPolicyHandler>();

  constructor() {
    this.#policies.set(DEFAULT_FIRING_POLICY, firstFound);
    this.#policies.set(PRIORITY_FIRING_POLICY, priority);
  }

  #register<K extends HandlerKind>(
    kind: K,
    table: Map<string, HandlerByKind[K]>,
    name: string,
    handler: HandlerByKind[K],
  ): void {
    if (name.length === 0) {
      throw new InvalidHandlerRegistrationError(kind, name, "name must not be empty");
    }
    if (typeof handler !== "function") {
      throw new InvalidHandlerRegistrationError(kind, name, "handler must be a function");
    }
    if (table.has(name)) throw new HandlerConflictError(kind, name);
    table.set(name, handler);
  }

  #resolve<K extends HandlerKind>(
    kind: K,
    table: ReadonlyMap<string, HandlerByKind[K]>,
    name: string,
  ): HandlerByKind[K] {
    const handler = table.get(name);
    if (handler === undefined) throw new HandlerNotFoundError(kind, name);
    return handler;
  }

  registerTransition(name: string, handler: TransitionHandler): void {
    this.#register("transition", this.#transitions, name, handler);
  }

  resolveTransition(name: string): TransitionHandler {
    return this.#resolve("transition", this.#transitions, name);
  }

  registerGuard(name: string, handler: GuardHandler): void {
    this.#register("guard", this.#guards, name, handler);
  }

  resolveGuard(name: string): GuardHandler {
    return this.#resolve("guard", this.#guards, name);
  }

  registerPredicate(name: string, handler: PredicateHandler): void {
    this.#register("predicate", this.#predicates, name, handler);
  }

  resolvePredicate(name: string): PredicateHandler {
    return this.#resolve("predicate", this.#predicates, name);
  }

  registerFiringPolicy(name: string, handler: FiringPolicyHandler): void {
    this.#register("firing-policy", this.#policies, name, handler);
  }

  resolveFiringPolicy(name: string): FiringPolicyHandler {
    return this.#resolve("firing-policy", this.#policies, name);
  }
}
