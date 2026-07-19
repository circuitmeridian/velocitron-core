import {parsedCelProgramsFor} from "../cel/compiled.js";
import {createDefaultCelAdapter} from "../cel/default.js";
import type {CelAdapter} from "../cel/types.js";
import type {FiringRecord, InjectionRecord, Journal} from "../journal/types.js";
import {
  DEFAULT_FIRING_POLICY,
  HandlerNotFoundError,
  type FiringContext,
  type HandlerError,
  type GuardHandler,
  type OutputTokens,
  type TokenBinding,
  type TransitionHandler,
  type TransitionHandlerOutput,
  HandlerRegistry,
} from "../registry/index.js";
import type {
  Arc,
  ConsumeArc,
  Marking,
  Net,
  ProduceArc,
  ProduceTemplate,
  ReadonlyJsonObject,
  Timer,
  Token,
  Transition,
} from "../schema/types.js";
import {
  DepositViolationError,
  EngineConfigurationError,
  TokenInjectionError,
  UnknownTransitionError,
} from "./errors.js";
import type {
  BatchInjectionResult,
  EngineClock,
  EngineIdFactory,
  EngineOptions,
  FireResult,
  FiringIdInput,
  InjectOptions,
  InjectionResult,
  InjectionIdInput,
  OptionalAttemptOptions,
  RunOptions,
  SelectedBinding,
  TickOptions,
  TimerMaturity,
  TokenPlacement,
} from "./types.js";

interface Binding {
  readonly tokens: TokenBinding;
  readonly consumed: TokenBinding;
}

interface Topology {
  readonly transitions: readonly Transition[];
  readonly transitionByName: ReadonlyMap<string, Transition>;
  readonly placeByName: ReadonlyMap<string, Net["places"][number]>;
  readonly inputArcs: ReadonlyMap<string, readonly ConsumeArc[]>;
  readonly bindingArcs: ReadonlyMap<string, readonly ConsumeArc[]>;
  readonly produceArcs: ReadonlyMap<string, readonly ProduceArc[]>;
}

interface CelPrograms {
  readonly adapter: CelAdapter;
  readonly programs: ReadonlyMap<string, unknown>;
}

/**
 * Internal: a produce template's `cel` failed at deposit (ADR 0023).
 *
 * An evaluation error or a non-object result while emitting a computed
 * fallback. Never escapes `fire` — it is caught and routed through the
 * ordinary deposit-contract-violation handling (D3), carrying this error's
 * message as the violation detail.
 */
class ProduceCelError extends Error {
  constructor(message: string) {
    super(message);
    this.name = new.target.name;
  }
}

const SYSTEM_CLOCK: EngineClock = Object.freeze({
  now: () => new Date().toISOString(),
});

const DEFAULT_IDS: EngineIdFactory = Object.freeze({
  firingId: ({netId, transition, attempt}: FiringIdInput) => `${netId}/${transition}/${attempt}`,
  injectionId: ({netId, place, attempt}: InjectionIdInput) => `${netId}/@inject/${place}/${attempt}`,
});

function jsonEqual(left: unknown, right: unknown): boolean {
  if (left === right) return true;
  if (typeof left !== typeof right || left === null || right === null) return false;
  if (Array.isArray(left)) {
    if (!Array.isArray(right) || left.length !== right.length) return false;
    for (let index = 0; index < left.length; index += 1) {
      if (!jsonEqual(left[index], right[index])) return false;
    }
    return true;
  }
  if (typeof left !== "object" || Array.isArray(right)) return false;
  const leftRecord = left as Readonly<Record<string, unknown>>;
  const rightRecord = right as Readonly<Record<string, unknown>>;
  const leftKeys = Object.keys(leftRecord);
  const rightKeys = Object.keys(rightRecord);
  if (leftKeys.length !== rightKeys.length) return false;
  for (const key of leftKeys) {
    if (!Object.hasOwn(rightRecord, key) || !jsonEqual(leftRecord[key], rightRecord[key])) {
      return false;
    }
  }
  return true;
}

function tokenEqual(left: Token, right: Token): boolean {
  return left.type === right.type && jsonEqual(left.data, right.data);
}

function removeEach(available: Token[], tokens: readonly Token[]): boolean {
  for (const token of tokens) {
    const index = available.findIndex((candidate) => tokenEqual(candidate, token));
    if (index < 0) return false;
    available.splice(index, 1);
  }
  return true;
}

function combinations(tokens: readonly Token[], weight: number): readonly (readonly Token[])[] {
  const result: Token[][] = [];
  const selected: Token[] = [];
  const visit = (start: number): void => {
    if (selected.length === weight) {
      result.push([...selected]);
      return;
    }
    const needed = weight - selected.length;
    for (let index = start; index <= tokens.length - needed; index += 1) {
      selected.push(tokens[index] as Token);
      visit(index + 1);
      selected.pop();
    }
  };
  visit(0);
  return result;
}

function *products<T>(groups: readonly (readonly T[])[], index = 0, selected: T[] = []): Generator<readonly T[]> {
  if (index === groups.length) {
    yield [...selected];
    return;
  }
  for (const value of groups[index] as readonly T[]) {
    selected.push(value);
    yield *products(groups, index + 1, selected);
    selected.pop();
  }
}

export class Engine {
  readonly registry: HandlerRegistry;
  readonly policy: string;
  readonly journal: Journal | undefined;
  readonly depositViolation: EngineOptions["depositViolation"];
  readonly maxConsecutiveFailures: number | null;
  readonly #clock: EngineClock;
  readonly #idFactory: EngineIdFactory;
  readonly #explicitCelAdapter: CelAdapter | undefined;
  readonly #defaultCelAdapter: CelAdapter;
  readonly #compiledByAdapter = new Map<CelAdapter, Map<string, unknown>>();
  readonly #topologyByNet = new WeakMap<Net, Topology>();

  constructor(registry: HandlerRegistry, options: EngineOptions = {}) {
    const depositViolation = options.depositViolation ?? (
      options.journal === undefined ? "raise" : "record_then_raise"
    );
    if (!(["raise", "record_then_raise", "record_then_drop"] as const).includes(depositViolation)) {
      throw new EngineConfigurationError(`unsupported deposit violation policy ${JSON.stringify(depositViolation)}`);
    }
    if (depositViolation !== "raise" && options.journal === undefined) {
      throw new EngineConfigurationError(`${JSON.stringify(depositViolation)} requires a journal`);
    }
    const failureBudget = options.maxConsecutiveFailures ?? null;
    if (failureBudget !== null && (!Number.isInteger(failureBudget) || failureBudget < 1)) {
      throw new EngineConfigurationError("maxConsecutiveFailures must be an integer greater than or equal to 1, or null");
    }

    const policy = options.policy ?? DEFAULT_FIRING_POLICY;
    registry.resolveFiringPolicy(policy);
    this.registry = registry;
    this.policy = policy;
    this.journal = options.journal;
    this.depositViolation = depositViolation;
    this.maxConsecutiveFailures = failureBudget;
    this.#clock = options.clock ?? SYSTEM_CLOCK;
    this.#idFactory = options.idFactory ?? DEFAULT_IDS;
    this.#explicitCelAdapter = options.celAdapter;
    this.#defaultCelAdapter = options.celAdapter ?? createDefaultCelAdapter();
  }

  #topology(net: Net): Topology {
    const cached = this.#topologyByNet.get(net);
    if (cached !== undefined) return cached;

    const transitionByName = new Map(net.transitions.map((transition) => [transition.name, transition]));
    const placeByName = new Map(net.places.map((place) => [place.name, place]));
    const inputArcs = new Map<string, ConsumeArc[]>();
    const bindingArcs = new Map<string, ConsumeArc[]>();
    const produceArcs = new Map<string, ProduceArc[]>();
    for (const arc of net.arcs) {
      if (arc.consume !== undefined) {
        const transition = arc.to.transition;
        const inputs = inputArcs.get(transition);
        if (inputs === undefined) inputArcs.set(transition, [arc]);
        else inputs.push(arc);
        if (arc.consume.mode === "consume" || arc.consume.mode === "read") {
          const bindings = bindingArcs.get(transition);
          if (bindings === undefined) bindingArcs.set(transition, [arc]);
          else bindings.push(arc);
        }
      } else {
        const transition = arc.from.transition;
        const outputs = produceArcs.get(transition);
        if (outputs === undefined) produceArcs.set(transition, [arc]);
        else outputs.push(arc);
      }
    }
    const topology: Topology = {
      transitions: net.transitions,
      transitionByName,
      placeByName,
      inputArcs,
      bindingArcs,
      produceArcs,
    };
    this.#topologyByNet.set(net, topology);
    return topology;
  }

  #requireTransition(net: Net, name: string): Transition {
    const transition = this.#topology(net).transitionByName.get(name);
    if (transition === undefined) throw new UnknownTransitionError(net.name, name);
    return transition;
  }

  #celPrograms(net: Net): CelPrograms {
    const parsed = parsedCelProgramsFor(net);
    if (
      parsed !== undefined &&
      (this.#explicitCelAdapter === undefined || this.#explicitCelAdapter === parsed.adapter)
    ) {
      return parsed;
    }
    let programs = this.#compiledByAdapter.get(this.#defaultCelAdapter);
    if (programs === undefined) {
      programs = new Map();
      this.#compiledByAdapter.set(this.#defaultCelAdapter, programs);
    }
    return {adapter: this.#defaultCelAdapter, programs};
  }

  #evaluate(net: Net, source: string, context: ReadonlyJsonObject): unknown {
    const cel = this.#celPrograms(net);
    let program = cel.programs.get(source);
    if (program === undefined) {
      program = cel.adapter.compile(source);
      if (cel.programs instanceof Map) cel.programs.set(source, program);
      else {
        let programs = this.#compiledByAdapter.get(cel.adapter);
        if (programs === undefined) {
          programs = new Map();
          this.#compiledByAdapter.set(cel.adapter, programs);
        }
        programs.set(source, program);
      }
    }
    return cel.adapter.evaluate(program, context);
  }

  #context(net: Net, transition: string, attempt: number): FiringContext {
    return {
      firingId: this.#idFactory.firingId({netId: net.name, transition, attempt}),
      netId: net.name,
      attempt,
      timestamps: {fired_at: this.#clock.now()},
    };
  }

  #predicateMatches(net: Net, arc: ConsumeArc, token: Token, context: FiringContext): boolean {
    if (token.type !== arc.consume.type) return false;
    const predicate = arc.consume.predicate;
    if (predicate === undefined) return true;
    if (predicate.cel !== undefined) {
      try {
        return this.#evaluate(net, predicate.cel, token.data) === true;
      } catch {
        return false;
      }
    }
    try {
      return this.registry.resolvePredicate(predicate.handler)({token, firingContext: context}) === true;
    } catch {
      return false;
    }
  }

  #inhibitsAllow(net: Net, transition: string, marking: Marking, context: FiringContext): boolean {
    for (const arc of this.#topology(net).inputArcs.get(transition) ?? []) {
      if (arc.consume.mode !== "inhibit" || arc.consume.correlate !== undefined) continue;
      const place = arc.from.place;
      if ((marking[place] ?? []).some((token) => this.#predicateMatches(net, arc, token, context))) {
        return false;
      }
    }
    return true;
  }

  #correlatedInhibitsAllow(
    net: Net,
    transition: string,
    marking: Marking,
    binding: TokenBinding,
    context: FiringContext,
  ): boolean {
    let bindingData: ReadonlyJsonObject | undefined;
    for (const arc of this.#topology(net).inputArcs.get(transition) ?? []) {
      const correlate = arc.consume.correlate;
      if (arc.consume.mode !== "inhibit" || correlate === undefined) continue;
      if (bindingData === undefined) {
        const data = Object.create(null) as Record<string, readonly ReadonlyJsonObject[]>;
        for (const [place, tokens] of Object.entries(binding)) {
          data[place] = tokens.map((token) => token.data);
        }
        bindingData = data;
      }
      for (const token of marking[arc.from.place] ?? []) {
        if (!this.#predicateMatches(net, arc, token, context)) continue;
        try {
          const result = this.#evaluate(net, correlate.cel, {token: token.data, binding: bindingData});
          if (result !== false) return false;
        } catch {
          return false;
        }
      }
    }
    return true;
  }

  #timerHolds(
    net: Net,
    timer: Timer,
    clockData: ReadonlyJsonObject,
    binding: TokenBinding,
  ): boolean {
    const environment = Object.create(null) as Record<string, ReadonlyJsonObject>;
    environment.clock = clockData;
    for (const [variable, place] of Object.entries(timer.bind ?? {})) {
      const token = binding[place]?.[0];
      if (token === undefined) return false;
      environment[variable] = token.data;
    }
    try {
      return this.#evaluate(net, timer.cel, environment) === true;
    } catch {
      return false;
    }
  }

  #arcCandidates(
    net: Net,
    arc: ConsumeArc,
    marking: Marking,
    context: FiringContext,
  ): readonly (readonly Token[])[] | null {
    const matching = (marking[arc.from.place] ?? []).filter(
      (token) => this.#predicateMatches(net, arc, token, context),
    );
    if (matching.length < arc.consume.weight) return null;
    return combinations(matching, arc.consume.weight);
  }

  #bindingFromSelection(
    selection: readonly (readonly Token[])[],
    arcs: readonly ConsumeArc[],
    consumeOnly: boolean,
  ): TokenBinding {
    const binding = Object.create(null) as Record<string, Token[]>;
    for (let index = 0; index < arcs.length; index += 1) {
      const arc = arcs[index] as ConsumeArc;
      if (consumeOnly && arc.consume.mode !== "consume") continue;
      const place = arc.from.place;
      (binding[place] ??= []).push(...(selection[index] as readonly Token[]));
    }
    return binding;
  }

  #isSubmultiset(binding: TokenBinding, marking: Marking): boolean {
    for (const [place, tokens] of Object.entries(binding)) {
      if (!removeEach([...(marking[place] ?? [])], tokens)) return false;
    }
    return true;
  }

  #selectBinding(
    net: Net,
    transition: Transition,
    marking: Marking,
    context: FiringContext,
  ): Binding | null {
    if (!this.#inhibitsAllow(net, transition.name, marking, context)) return null;

    let clockData: ReadonlyJsonObject | undefined;
    if (transition.timer !== undefined) {
      clockData = marking[transition.timer.clock]?.[0]?.data;
      if (clockData === undefined) return null;
    }

    const arcs = this.#topology(net).bindingArcs.get(transition.name) ?? [];
    const candidates: (readonly (readonly Token[])[])[] = [];
    for (const arc of arcs) {
      const choices = this.#arcCandidates(net, arc, marking, context);
      if (choices === null) return null;
      candidates.push(choices);
    }

    let guard: GuardHandler | undefined;
    if (transition.guard !== undefined) {
      try {
        guard = this.registry.resolveGuard(transition.guard);
      } catch (error) {
        if (error instanceof HandlerNotFoundError) return null;
        throw error;
      }
    }

    for (const selection of products(candidates)) {
      const binding = this.#bindingFromSelection(selection, arcs, false);
      if (!this.#isSubmultiset(binding, marking)) continue;
      if (!this.#correlatedInhibitsAllow(net, transition.name, marking, binding, context)) continue;
      if (
        transition.timer !== undefined &&
        clockData !== undefined &&
        !this.#timerHolds(net, transition.timer, clockData, binding)
      ) {
        continue;
      }
      if (guard !== undefined) {
        try {
          if (guard({
            transitionId: transition.name,
            inputTokens: binding,
            firingContext: context,
          }) !== true) {
            continue;
          }
        } catch {
          return null;
        }
      }
      return {
        tokens: binding,
        consumed: this.#bindingFromSelection(selection, arcs, true),
      };
    }
    return null;
  }

  selectBinding(
    net: Net,
    transition: string,
    marking: Marking,
    options: OptionalAttemptOptions = {},
  ): SelectedBinding {
    const declared = this.#requireTransition(net, transition);
    const context = this.#context(net, transition, options.attempt ?? 0);
    return this.#selectBinding(net, declared, marking, context)?.tokens ?? null;
  }

  enabledTransitions(
    net: Net,
    marking: Marking,
    options: OptionalAttemptOptions = {},
  ): readonly string[] {
    const enabled: string[] = [];
    const attempt = options.attempt ?? 0;
    for (const transition of this.#topology(net).transitions) {
      const context = this.#context(net, transition.name, attempt);
      if (this.#selectBinding(net, transition, marking, context) !== null) {
        enabled.push(transition.name);
      }
    }
    return enabled;
  }

  #record(
    context: FiringContext,
    net: Net,
    transition: string,
    attempt: number,
    status: FiringRecord["status"],
    inputTokens: TokenBinding,
    outputTokens: OutputTokens,
    error: HandlerError | null,
    metadata: ReadonlyJsonObject,
  ): FiringRecord {
    return {
      firingId: context.firingId,
      netId: net.name,
      transition,
      attempt,
      status,
      inputTokens,
      outputTokens,
      error,
      metadata,
      timestamps: context.timestamps,
    };
  }

  #failed(
    context: FiringContext,
    net: Net,
    transition: string,
    attempt: number,
    error: HandlerError | null,
    metadata: ReadonlyJsonObject = {},
  ): FiringRecord {
    const record = this.#record(context, net, transition, attempt, "failed", {}, {}, error, metadata);
    this.journal?.recordFiring(record);
    return record;
  }

  #templates(net: Net, transition: string): readonly ProduceTemplate[] {
    return (this.#topology(net).produceArcs.get(transition) ?? []).map((arc) => arc.produce);
  }

  #hasDepositViolation(outputTokens: OutputTokens, templates: readonly ProduceTemplate[]): boolean {
    for (const [destination, tokens] of Object.entries(outputTokens)) {
      for (const token of tokens) {
        if (!templates.some((template) => (
          template.destination === destination && template.type === token.type
        ))) {
          return true;
        }
      }
    }
    return false;
  }

  #depositedTokens(
    net: Net,
    outputTokens: OutputTokens,
    templates: readonly ProduceTemplate[],
    bindingTokens: TokenBinding,
  ): OutputTokens {
    const deposited = Object.create(null) as Record<string, Token[]>;
    for (const [destination, tokens] of Object.entries(outputTokens)) {
      if (tokens.length > 0) deposited[destination] = [...tokens];
    }
    // ADR 0023: the environment for computed fallbacks is the same
    // place-keyed bound-token-data map correlate uses; built once, lazily.
    let bindingData: ReadonlyJsonObject | undefined;
    for (const template of templates) {
      const handlerTokens = outputTokens[template.destination] ?? [];
      if (handlerTokens.some((token) => token.type === template.type)) continue;
      if (template.data !== undefined) {
        (deposited[template.destination] ??= []).push({type: template.type, data: template.data});
      } else if (template.cel !== undefined) {
        if (bindingData === undefined) {
          const data = Object.create(null) as Record<string, readonly ReadonlyJsonObject[]>;
          for (const [place, tokens] of Object.entries(bindingTokens)) {
            data[place] = tokens.map((token) => token.data);
          }
          bindingData = data;
        }
        let result: unknown;
        try {
          result = this.#evaluate(net, template.cel, {binding: bindingData});
        } catch (error) {
          throw new ProduceCelError(
            `produce cel into ${JSON.stringify(template.destination)} failed to evaluate: ${
              error instanceof Error ? error.message : String(error)
            }`,
          );
        }
        if (typeof result !== "object" || result === null || Array.isArray(result)) {
          throw new ProduceCelError(
            `produce cel into ${JSON.stringify(template.destination)} must yield a JSON object, got ${
              result === null ? "null" : Array.isArray(result) ? "array" : typeof result
            }`,
          );
        }
        (deposited[template.destination] ??= []).push({
          type: template.type,
          data: result as ReadonlyJsonObject,
        });
      }
    }
    return deposited;
  }

  #commit(marking: Marking, consumed: TokenBinding, deposited: OutputTokens): Marking {
    const touched = new Set([...Object.keys(consumed), ...Object.keys(deposited)]);
    if (touched.size === 0) return marking;
    const next = Object.assign(
      Object.create(null) as Record<string, readonly Token[]>,
      marking,
    );
    for (const place of touched) {
      const tokens = [...(marking[place] ?? [])];
      removeEach(tokens, consumed[place] ?? []);
      tokens.push(...(deposited[place] ?? []));
      next[place] = tokens;
    }
    return next;
  }

  #depositViolationResult(
    context: FiringContext,
    net: Net,
    transition: string,
    attempt: number,
    metadata: ReadonlyJsonObject,
    marking: Marking,
    detail?: string,
  ): FireResult {
    const error = new DepositViolationError(transition, detail);
    const record = this.#record(
      context,
      net,
      transition,
      attempt,
      "failed",
      {},
      {},
      {type: "DepositViolation", message: error.message},
      metadata,
    );
    if (this.depositViolation === "raise") throw error;
    this.journal?.recordDepositViolation(record);
    if (this.depositViolation === "record_then_raise") throw error;
    return {marking, record};
  }

  fire(net: Net, marking: Marking, transition: string, options: {readonly attempt: number}): FireResult {
    const declared = this.#requireTransition(net, transition);
    const context = this.#context(net, transition, options.attempt);
    const binding = this.#selectBinding(net, declared, marking, context);
    if (binding === null) {
      return {
        marking,
        record: this.#failed(context, net, transition, options.attempt, {
          type: "NotEnabled",
          message: `transition ${JSON.stringify(transition)} is not enabled`,
        }),
      };
    }
    if (declared.handler === undefined) {
      return {
        marking,
        record: this.#failed(context, net, transition, options.attempt, {
          type: "HandlerNotFound",
          message: `transition ${JSON.stringify(transition)} has no handler`,
        }),
      };
    }

    let handler: TransitionHandler;
    try {
      handler = this.registry.resolveTransition(declared.handler);
    } catch (error) {
      if (!(error instanceof HandlerNotFoundError)) throw error;
      return {
        marking,
        record: this.#failed(context, net, transition, options.attempt, {
          type: "HandlerNotFound",
          message: `handler ${JSON.stringify(declared.handler)} is not registered`,
        }),
      };
    }

    const output: TransitionHandlerOutput = handler({
      transitionId: transition,
      inputTokens: binding.tokens,
      firingContext: context,
    });
    const metadata = output.metadata ?? {};
    if (output.status === "failed") {
      return {
        marking,
        record: this.#failed(context, net, transition, options.attempt, output.error, metadata),
      };
    }

    const outputTokens = output.outputTokens ?? {};
    const templates = this.#templates(net, transition);
    if (this.#hasDepositViolation(outputTokens, templates)) {
      return this.#depositViolationResult(
        context,
        net,
        transition,
        options.attempt,
        metadata,
        marking,
      );
    }
    let deposited: OutputTokens;
    try {
      deposited = this.#depositedTokens(net, outputTokens, templates, binding.tokens);
    } catch (error) {
      if (!(error instanceof ProduceCelError)) throw error;
      return this.#depositViolationResult(
        context,
        net,
        transition,
        options.attempt,
        metadata,
        marking,
        error.message,
      );
    }
    const next = this.#commit(marking, binding.consumed, deposited);
    const record = this.#record(
      context,
      net,
      transition,
      options.attempt,
      "completed",
      binding.tokens,
      deposited,
      null,
      metadata,
    );
    this.journal?.recordFiring(record);
    return {marking: next, record};
  }

  validate(net: Net): void {
    for (const transition of net.transitions) {
      if (transition.handler !== undefined) this.registry.resolveTransition(transition.handler);
      if (transition.guard !== undefined) this.registry.resolveGuard(transition.guard);
    }
    for (const arc of net.arcs) {
      if (arc.consume?.predicate?.handler !== undefined) {
        this.registry.resolvePredicate(arc.consume.predicate.handler);
      }
    }
  }

  #validateInjection(net: Net, place: string, token: Token): void {
    const declared = this.#topology(net).placeByName.get(place);
    if (declared === undefined) {
      throw new TokenInjectionError(net.name, place, `net ${JSON.stringify(net.name)} has no place named ${JSON.stringify(place)}`);
    }
    if (!declared.accepts.includes(token.type)) {
      throw new TokenInjectionError(
        net.name,
        place,
        `place ${JSON.stringify(place)} does not accept token type ${JSON.stringify(token.type)}`,
      );
    }
  }

  inject(
    net: Net,
    marking: Marking,
    place: string,
    token: Token,
    options: InjectOptions,
  ): InjectionResult {
    this.#validateInjection(net, place, token);
    const existing = marking[place] ?? [];
    const replace = options.replace ?? false;
    const next = Object.assign(
      Object.create(null) as Record<string, readonly Token[]>,
      marking,
    );
    next[place] = replace ? [token] : [...existing, token];
    const record: InjectionRecord = {
      injectionId: this.#idFactory.injectionId({netId: net.name, place, attempt: options.attempt}),
      netId: net.name,
      place,
      attempt: options.attempt,
      kind: replace ? "update" : "inject",
      tokens: [token],
      replaced: replace ? existing : [],
      timestamps: {fired_at: this.#clock.now()},
    };
    this.journal?.recordInjection(record);
    return {marking: next, record};
  }

  injectMany(
    net: Net,
    marking: Marking,
    placements: readonly TokenPlacement[],
    options: {readonly attempt: number},
  ): BatchInjectionResult {
    for (const placement of placements) {
      this.#validateInjection(net, placement.place, placement.token);
    }
    const additions = new Map<string, Token[]>();
    for (const placement of placements) {
      const tokens = additions.get(placement.place);
      if (tokens === undefined) additions.set(placement.place, [placement.token]);
      else tokens.push(placement.token);
    }
    const next: Record<string, readonly Token[]> = placements.length === 0
      ? marking
      : Object.assign(
          Object.create(null) as Record<string, readonly Token[]>,
          marking,
        );
    for (const [place, tokens] of additions) {
      next[place] = [...(marking[place] ?? []), ...tokens];
    }
    const records: InjectionRecord[] = [];
    for (const placement of placements) {
      const record: InjectionRecord = {
        injectionId: this.#idFactory.injectionId({
          netId: net.name,
          place: placement.place,
          attempt: options.attempt,
        }),
        netId: net.name,
        place: placement.place,
        attempt: options.attempt,
        kind: "inject",
        tokens: [placement.token],
        replaced: [],
        timestamps: {fired_at: this.#clock.now()},
      };
      this.journal?.recordInjection(record);
      records.push(record);
    }
    return {marking: next, records};
  }

  timerMaturities(net: Net, marking: Marking): readonly TimerMaturity[] {
    const maturities: TimerMaturity[] = [];
    for (const transition of net.transitions) {
      const timer = transition.timer;
      if (timer?.maturity === undefined) continue;
      const clockData = marking[timer.clock]?.[0]?.data;
      if (clockData === undefined) continue;
      const context = this.#context(net, transition.name, 0);
      if (!this.#inhibitsAllow(net, transition.name, marking, context)) continue;
      const arcs = this.#topology(net).bindingArcs.get(transition.name) ?? [];
      const candidates: (readonly (readonly Token[])[])[] = [];
      let viable = true;
      for (const arc of arcs) {
        const choices = this.#arcCandidates(net, arc, marking, context);
        if (choices === null) {
          viable = false;
          break;
        }
        candidates.push(choices);
      }
      if (!viable) continue;
      for (const selection of products(candidates)) {
        const binding = this.#bindingFromSelection(selection, arcs, false);
        if (!this.#isSubmultiset(binding, marking)) continue;
        if (!this.#correlatedInhibitsAllow(net, transition.name, marking, binding, context)) continue;
        if (this.#timerHolds(net, timer, clockData, binding)) continue;
        const environment = Object.create(null) as Record<string, ReadonlyJsonObject>;
        environment.clock = clockData;
        let complete = true;
        for (const [variable, place] of Object.entries(timer.bind ?? {})) {
          const token = binding[place]?.[0];
          if (token === undefined) {
            complete = false;
            break;
          }
          environment[variable] = token.data;
        }
        if (!complete) continue;
        try {
          const raw = this.#evaluate(net, timer.maturity, environment);
          const current = Number(clockData.now);
          const at = typeof raw === "number" ? raw : Number.NaN;
          if (Number.isFinite(current) && Number.isFinite(at) && at > current) {
            maturities.push({transition: transition.name, clock: timer.clock, at});
          }
        } catch {
          // An unschedulable maturity is advisory and never changes enablement.
        }
      }
    }
    return maturities;
  }

  tick(
    net: Net,
    marking: Marking,
    place: string,
    token: Token,
    options: TickOptions = {},
  ): Marking {
    const injected = this.inject(net, marking, place, token, {
      attempt: options.attempt ?? 0,
      replace: true,
    });
    return this.run(net, injected.marking, {maxSteps: options.maxSteps});
  }

  run(net: Net, marking: Marking, options: RunOptions = {}): Marking {
    const maxSteps = options.maxSteps ?? 1_000;
    const priorities = new Map(net.transitions.map((transition) => [
      transition.name,
      transition.priority ?? 0,
    ]));
    const failures = new Map<string, number>();
    let current = marking;
    for (let step = 0; step < maxSteps; step += 1) {
      let enabled = [...this.enabledTransitions(net, current, {attempt: step})];
      if (this.maxConsecutiveFailures !== null) {
        enabled = enabled.filter(
          (name) => (failures.get(name) ?? 0) < this.maxConsecutiveFailures!,
        );
      }
      if (enabled.length === 0) break;
      const priorityInput = Object.create(null) as Record<string, number>;
      const failureInput = Object.create(null) as Record<string, number>;
      for (const name of enabled) {
        priorityInput[name] = priorities.get(name) ?? 0;
        failureInput[name] = failures.get(name) ?? 0;
      }
      const choice = this.registry.resolveFiringPolicy(this.policy)({
        marking: current,
        enabledTransitions: enabled,
        priorities: priorityInput,
        consecutiveFailures: failureInput,
      });
      if (choice === null) break;
      const result = this.fire(net, current, choice, {attempt: step});
      current = result.marking;
      if (result.record.status === "failed") {
        failures.set(choice, (failures.get(choice) ?? 0) + 1);
      } else {
        failures.clear();
      }
    }
    return current;
  }
}
