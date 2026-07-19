import {rememberParsedCelPrograms} from "../cel/compiled.js";
import {createDefaultCelAdapter} from "../cel/default.js";
import {normalizeCelError} from "../cel/errors.js";
import type {CelAdapter, CelDiagnostic} from "../cel/types.js";
import {NetValidationError, type NetValidationIssue} from "./errors.js";
import {deepFreezeJson} from "./json.js";
import type {
  Arc,
  ConsumeArc,
  ConsumePattern,
  Correlate,
  Marking,
  Net,
  Place,
  Predicate,
  ProduceArc,
  ReadonlyJsonObject,
  Timer,
  Transition,
} from "./types.js";
import {validateNetShape} from "./validate.js";

export interface ParseNetOptions {
  readonly celAdapter?: CelAdapter;
}

interface RawConsumePattern {
  readonly type: string;
  readonly mode?: ConsumePattern["mode"];
  readonly weight?: number;
  readonly predicate?: Predicate;
  readonly correlate?: Correlate;
}

interface RawConsumeArc extends Omit<ConsumeArc, "consume"> {
  readonly consume: RawConsumePattern;
}

type RawArc = RawConsumeArc | ProduceArc;

interface RawNetDocument extends Omit<Net, "arcs"> {
  readonly arcs: readonly RawArc[];
}

function normalizedNet(document: RawNetDocument): Net {
  const places: Place[] = document.places.map((place) => ({
    name: place.name,
    accepts: [...place.accepts],
    ...(place.port === undefined ? {} : {port: {...place.port}}),
    ...(place.capacityPerColorKey === undefined
      ? {}
      : {
          capacityPerColorKey: {
            key: Array.isArray(place.capacityPerColorKey.key)
              ? [...place.capacityPerColorKey.key]
              : place.capacityPerColorKey.key,
            max: place.capacityPerColorKey.max,
          },
        }),
    ...(place.description === undefined ? {} : {description: place.description}),
    ...(place.annotations === undefined ? {} : {annotations: place.annotations}),
  }));
  const transitions: Transition[] = document.transitions.map((transition) => {
    const timer: Timer | undefined = transition.timer === undefined
      ? undefined
      : {
          clock: transition.timer.clock,
          cel: transition.timer.cel,
          ...(transition.timer.bind === undefined ? {} : {bind: {...transition.timer.bind}}),
          ...(transition.timer.maturity === undefined
            ? {}
            : {maturity: transition.timer.maturity}),
        };
    return {
      name: transition.name,
      ...(transition.handler === undefined ? {} : {handler: transition.handler}),
      ...(transition.guard === undefined ? {} : {guard: transition.guard}),
      ...(transition.priority === undefined ? {} : {priority: transition.priority}),
      ...(timer === undefined ? {} : {timer}),
      ...(transition.description === undefined ? {} : {description: transition.description}),
      ...(transition.annotations === undefined ? {} : {annotations: transition.annotations}),
    };
  });
  const arcs: Arc[] = document.arcs.map((arc) => {
    if (arc.consume !== undefined) {
      const consumeArc = arc as RawConsumeArc;
      const consume = consumeArc.consume;
      return {
        from: {...consumeArc.from},
        to: {...consumeArc.to},
        consume: {
          type: consume.type,
          mode: consume.mode ?? "consume",
          weight: consume.weight ?? 1,
          ...(consume.predicate === undefined
            ? {}
            : {predicate: {...consume.predicate}}),
          ...(consume.correlate === undefined
            ? {}
            : {correlate: {...consume.correlate}}),
        },
        ...(consumeArc.description === undefined ? {} : {description: consumeArc.description}),
        ...(consumeArc.annotations === undefined ? {} : {annotations: consumeArc.annotations}),
      };
    }
    const produceArc = arc as ProduceArc;
    return {
      from: {...produceArc.from},
      to: {...produceArc.to},
      produce: {
        type: produceArc.produce.type,
        destination: produceArc.produce.destination,
        ...(produceArc.produce.data === undefined ? {} : {data: produceArc.produce.data}),
        ...(produceArc.produce.cel === undefined ? {} : {cel: produceArc.produce.cel}),
      },
      ...(produceArc.description === undefined ? {} : {description: produceArc.description}),
      ...(produceArc.annotations === undefined ? {} : {annotations: produceArc.annotations}),
    };
  });

  return {
    name: document.name,
    places,
    transitions,
    arcs,
    ...(document.initialMarking === undefined
      ? {}
      : {initialMarking: document.initialMarking as Marking}),
    ...(document.description === undefined ? {} : {description: document.description}),
    ...(document.annotations === undefined ? {} : {annotations: document.annotations}),
  };
}

function celDetails(diagnostic: CelDiagnostic): ReadonlyJsonObject {
  return {
    phase: diagnostic.phase,
    code: diagnostic.code,
    summary: diagnostic.summary,
    ...(diagnostic.range === undefined ? {} : {range: {...diagnostic.range}}),
  };
}

function semanticIssues(
  net: Net,
  adapter: CelAdapter,
): {readonly issues: readonly NetValidationIssue[]; readonly programs: ReadonlyMap<string, unknown>} {
  const issues: NetValidationIssue[] = [];
  const programs = new Map<string, unknown>();
  const placeIndexes = new Map<string, number>();
  const transitionIndexes = new Map<string, number>();

  net.places.forEach((place, index) => {
    const prior = placeIndexes.get(place.name);
    if (prior !== undefined) {
      issues.push({
        code: "place.duplicate_name",
        path: `/places/${index}/name`,
        message: `duplicate place name: ${JSON.stringify(place.name)}`,
        details: {firstPath: `/places/${prior}/name`, name: place.name},
      });
    } else {
      placeIndexes.set(place.name, index);
    }
    if (place.port !== undefined && !place.accepts.includes(place.port.type)) {
      issues.push({
        code: "port.type_not_accepted",
        path: `/places/${index}/port/type`,
        message: `port type ${JSON.stringify(place.port.type)} not accepted by place ${JSON.stringify(place.name)}`,
        details: {accepts: place.accepts, place: place.name, type: place.port.type},
      });
    }
  });

  net.transitions.forEach((transition, index) => {
    const prior = transitionIndexes.get(transition.name);
    if (prior !== undefined) {
      issues.push({
        code: "transition.duplicate_name",
        path: `/transitions/${index}/name`,
        message: `duplicate transition name: ${JSON.stringify(transition.name)}`,
        details: {firstPath: `/transitions/${prior}/name`, name: transition.name},
      });
    } else {
      transitionIndexes.set(transition.name, index);
    }
  });

  const compile = (
    source: string,
    path: string,
    code: NetValidationIssue["code"],
    context: string,
  ): void => {
    if (programs.has(source)) return;
    try {
      const compiled = adapter.compile(source);
      if (
        (typeof compiled === "object" || typeof compiled === "function") &&
        compiled !== null &&
        "then" in compiled &&
        typeof compiled.then === "function"
      ) {
        throw new Error("CEL compilation must be synchronous");
      }
      programs.set(source, compiled);
    } catch (error) {
      const normalized = normalizeCelError(error, "compile");
      issues.push({
        code,
        path,
        message: `invalid CEL expression in ${context}: ${JSON.stringify(source)} (${normalized.diagnostic.summary})`,
        details: {cel: celDetails(normalized.diagnostic), source},
      });
    }
  };

  net.arcs.forEach((arc, index) => {
    const path = `/arcs/${index}`;
    if (arc.consume !== undefined) {
      const consume = arc.consume;
      const from = arc.from;
      const to = arc.to;
      if (
        !("place" in from) ||
        typeof from.place !== "string" ||
        !("transition" in to) ||
        typeof to.transition !== "string"
      ) {
        issues.push({
          code: "arc.consume.direction",
          path,
          message: "consume arc must be place to transition",
        });
        return;
      }
      const sourcePlace = from.place;
      const transition = to.transition;
      const placeIndex = placeIndexes.get(sourcePlace);
      if (placeIndex === undefined) {
        issues.push({
          code: "arc.place_undeclared",
          path: `${path}/from/place`,
          message: `arc references undeclared place: ${JSON.stringify(sourcePlace)}`,
          details: {place: sourcePlace},
        });
      }
      if (!transitionIndexes.has(transition)) {
        issues.push({
          code: "arc.transition_undeclared",
          path: `${path}/to/transition`,
          message: `arc references undeclared transition: ${JSON.stringify(transition)}`,
          details: {transition},
        });
      }
      const place = placeIndex === undefined ? undefined : net.places[placeIndex];
      if (place !== undefined && !place.accepts.includes(consume.type)) {
        issues.push({
          code: "arc.consume.type_not_accepted",
          path: `${path}/consume/type`,
          message: `consume type ${JSON.stringify(consume.type)} not accepted by place ${JSON.stringify(sourcePlace)}`,
          details: {place: sourcePlace, type: consume.type},
        });
      }
      if (consume.weight < 1 || !Number.isInteger(consume.weight)) {
        issues.push({
          code: "arc.consume.weight_invalid",
          path: `${path}/consume/weight`,
          message: `consume weight must be >= 1 (arc from ${JSON.stringify(sourcePlace)})`,
        });
      }
      if (consume.mode === "inhibit" && consume.weight !== 1) {
        issues.push({
          code: "arc.inhibit.weight_not_allowed",
          path: `${path}/consume/weight`,
          message: `weight is not allowed on inhibit arcs (arc from ${JSON.stringify(sourcePlace)})`,
        });
      }
      if (consume.correlate !== undefined && consume.mode !== "inhibit") {
        issues.push({
          code: "arc.correlate.mode_invalid",
          path: `${path}/consume/correlate`,
          message: `correlate is only allowed on inhibit arcs (arc from ${JSON.stringify(sourcePlace)} has mode ${JSON.stringify(consume.mode)})`,
        });
      }
      if (consume.predicate !== undefined && typeof consume.predicate.cel === "string") {
        compile(
          consume.predicate.cel,
          `${path}/consume/predicate/cel`,
          "arc.predicate.cel_invalid",
          `consume predicate on arc from ${JSON.stringify(sourcePlace)}`,
        );
      }
      if (consume.correlate !== undefined) {
        compile(
          consume.correlate.cel,
          `${path}/consume/correlate/cel`,
          "arc.correlate.cel_invalid",
          `correlate on inhibit arc from ${JSON.stringify(sourcePlace)}`,
        );
      }
      return;
    }

    const produce = arc.produce;
    const from = arc.from;
    const to = arc.to;
    if (
      produce === undefined ||
      !("transition" in from) ||
      typeof from.transition !== "string" ||
      !("place" in to) ||
      typeof to.place !== "string"
    ) {
      issues.push({
        code: "arc.produce.direction",
        path,
        message: "produce arc must be transition to place",
      });
      return;
    }
    const transition = from.transition;
    const destinationPlace = to.place;
    if (!transitionIndexes.has(transition)) {
      issues.push({
        code: "arc.transition_undeclared",
        path: `${path}/from/transition`,
        message: `arc references undeclared transition: ${JSON.stringify(transition)}`,
        details: {transition},
      });
    }
    const placeIndex = placeIndexes.get(destinationPlace);
    if (placeIndex === undefined) {
      issues.push({
        code: "arc.place_undeclared",
        path: `${path}/to/place`,
        message: `arc references undeclared place: ${JSON.stringify(destinationPlace)}`,
        details: {place: destinationPlace},
      });
    }
    if (produce.destination !== destinationPlace) {
      issues.push({
        code: "arc.produce.destination_mismatch",
        path: `${path}/produce/destination`,
        message: `produce destination ${JSON.stringify(produce.destination)} must equal arc to-place ${JSON.stringify(destinationPlace)}`,
        details: {destination: produce.destination, toPlace: destinationPlace},
      });
    }
    const place = placeIndex === undefined ? undefined : net.places[placeIndex];
    if (place !== undefined && !place.accepts.includes(produce.type)) {
      issues.push({
        code: "arc.produce.type_not_accepted",
        path: `${path}/produce/type`,
        message: `produce type ${JSON.stringify(produce.type)} not accepted by place ${JSON.stringify(destinationPlace)}`,
        details: {place: destinationPlace, type: produce.type},
      });
    }
    // Rule 14 (ADR 0023): at most one of literal data and computed cel; the
    // cel must compile at parse like every other inline expression (D6).
    if (produce.cel !== undefined) {
      if (produce.data !== undefined) {
        issues.push({
          code: "arc.produce.cel_data_exclusive",
          path: `${path}/produce/cel`,
          message: `produce template into ${JSON.stringify(destinationPlace)} declares both "data" and "cel"; they are mutually exclusive`,
          details: {place: destinationPlace},
        });
      }
      compile(
        produce.cel,
        `${path}/produce/cel`,
        "arc.produce.cel_invalid",
        `produce template into ${JSON.stringify(destinationPlace)}`,
      );
    }
  });

  const bindingSources = new Map<string, Set<string>>();
  net.arcs.forEach((arc) => {
    const consume = arc.consume;
    if (
      consume !== undefined &&
      (consume.mode === "consume" || consume.mode === "read") &&
      "place" in arc.from &&
      typeof arc.from.place === "string" &&
      "transition" in arc.to &&
      typeof arc.to.transition === "string"
    ) {
      const sources = bindingSources.get(arc.to.transition) ?? new Set<string>();
      sources.add(arc.from.place);
      bindingSources.set(arc.to.transition, sources);
    }
  });

  net.transitions.forEach((transition, index) => {
    const timer = transition.timer;
    if (timer === undefined) return;
    const path = `/transitions/${index}/timer`;
    if (!placeIndexes.has(timer.clock)) {
      issues.push({
        code: "timer.clock_undeclared",
        path: `${path}/clock`,
        message: `timer on transition ${JSON.stringify(transition.name)} references undeclared clock place: ${JSON.stringify(timer.clock)}`,
        details: {clock: timer.clock, transition: transition.name},
      });
    }
    const sources = bindingSources.get(transition.name) ?? new Set<string>();
    for (const [variable, place] of Object.entries(timer.bind ?? {})) {
      if (variable === "clock") {
        issues.push({
          code: "timer.bind.clock_reserved",
          path: `${path}/bind/clock`,
          message: `timer on transition ${JSON.stringify(transition.name)}: bind variable "clock" is reserved for the clock token`,
        });
      }
      if (!sources.has(place)) {
        issues.push({
          code: "timer.bind.source_invalid",
          path: `${path}/bind/${variable.replaceAll("~", "~0").replaceAll("/", "~1")}`,
          message: `timer on transition ${JSON.stringify(transition.name)}: bind variable ${JSON.stringify(variable)} names place ${JSON.stringify(place)}, which is not a source place of any consume- or read-mode arc of that transition`,
          details: {place, transition: transition.name, variable},
        });
      }
    }
    compile(
      timer.cel,
      `${path}/cel`,
      "timer.cel_invalid",
      `timer on transition ${JSON.stringify(transition.name)}`,
    );
    if (timer.maturity !== undefined) {
      compile(
        timer.maturity,
        `${path}/maturity`,
        "timer.maturity.cel_invalid",
        `timer maturity on transition ${JSON.stringify(transition.name)}`,
      );
    }
  });

  return {issues, programs};
}

export function parseNet(input: unknown, options: ParseNetOptions = {}): Net {
  const document = validateNetShape(input) as unknown as RawNetDocument;
  const net = normalizedNet(document);
  const adapter = options.celAdapter ?? createDefaultCelAdapter();
  const validation = semanticIssues(net, adapter);
  if (validation.issues.length > 0) throw new NetValidationError(validation.issues);
  deepFreezeJson(net);
  rememberParsedCelPrograms(net, adapter, validation.programs);
  return net;
}
