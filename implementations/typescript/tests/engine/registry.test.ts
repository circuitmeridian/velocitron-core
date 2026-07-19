import {describe, expect, it} from "vitest";
import {
  DEFAULT_FIRING_POLICY,
  HandlerConflictError,
  HandlerNotFoundError,
  HandlerRegistry,
  InvalidHandlerRegistrationError,
  PRIORITY_FIRING_POLICY,
} from "../../src/registry/index.js";
import type {TransitionHandler} from "../../src/registry/types.js";

const completed: TransitionHandler = () => ({
  status: "completed",
  outputTokens: {},
  error: null,
  metadata: {},
});

describe("HandlerRegistry", () => {
  it("keeps handler kinds in independent namespaces and resolves exact registrations", () => {
    // given: one name deliberately reused across all handler kinds
    const registry = new HandlerRegistry();
    const guard = () => true;
    const predicate = () => false;
    const policy = () => null;

    // when: each callable is registered under its kind-specific API
    registry.registerTransition("shared", completed);
    registry.registerGuard("shared", guard);
    registry.registerPredicate("shared", predicate);
    registry.registerFiringPolicy("shared", policy);

    // then: lookup returns the exact callable without a cross-kind collision
    expect(registry.resolveTransition("shared")).toBe(completed);
    expect(registry.resolveGuard("shared")).toBe(guard);
    expect(registry.resolvePredicate("shared")).toBe(predicate);
    expect(registry.resolveFiringPolicy("shared")).toBe(policy);
  });

  it("rejects duplicate same-kind registration instead of silently replacing behavior", () => {
    // given: an existing transition handler
    const registry = new HandlerRegistry();
    registry.registerTransition("work", completed);

    // when/then: a duplicate is a typed conflict and the original remains installed
    // Bite: an overwrite implementation makes both assertions fail observably.
    expect(() => registry.registerTransition("work", () => ({
      status: "failed",
      outputTokens: {},
      error: {type: "wrong", message: "wrong"},
      metadata: {},
    }))).toThrowError(expect.objectContaining({
      name: "HandlerConflictError",
      kind: "transition",
      handlerName: "work",
    }));
    expect(registry.resolveTransition("work")).toBe(completed);
    expect(HandlerConflictError).toBeTypeOf("function");
  });

  it("reports invalid registrations and missing per-kind lookups with typed details", () => {
    // given: a fresh registry
    const registry = new HandlerRegistry();

    // when/then: invalid registration and a lookup miss remain distinct errors
    expect(() => registry.registerGuard("", () => true)).toThrow(InvalidHandlerRegistrationError);
    expect(() => registry.resolvePredicate("absent")).toThrowError(expect.objectContaining({
      name: "HandlerNotFoundError",
      kind: "predicate",
      handlerName: "absent",
    }));
    expect(HandlerNotFoundError).toBeTypeOf("function");
  });

  it("installs deterministic first-found and stable priority policies", () => {
    // given: the two reserved policies on a fresh registry
    const registry = new HandlerRegistry();
    const input = {
      marking: {},
      enabledTransitions: ["early", "late", "tie"],
      priorities: {early: 0, late: 5, tie: 5},
      consecutiveFailures: {early: 0, late: 0, tie: 0},
    } as const;

    // when/then: declaration order wins by default and breaks priority ties
    expect(registry.resolveFiringPolicy(DEFAULT_FIRING_POLICY)(input)).toBe("early");
    expect(registry.resolveFiringPolicy(PRIORITY_FIRING_POLICY)(input)).toBe("late");
    expect(registry.resolveFiringPolicy(DEFAULT_FIRING_POLICY)({...input, enabledTransitions: []}))
      .toBeNull();
  });
});
