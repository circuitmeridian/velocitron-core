import type {HandlerKind} from "./types.js";

export class HandlerRegistryError extends Error {
  constructor(message: string) {
    super(message);
    this.name = new.target.name;
  }
}

export class InvalidHandlerRegistrationError extends HandlerRegistryError {
  readonly kind: HandlerKind;
  readonly handlerName: string;

  constructor(kind: HandlerKind, handlerName: string, reason: string) {
    super(`cannot register ${kind} handler ${JSON.stringify(handlerName)}: ${reason}`);
    this.kind = kind;
    this.handlerName = handlerName;
  }
}

export class HandlerConflictError extends HandlerRegistryError {
  readonly kind: HandlerKind;
  readonly handlerName: string;

  constructor(kind: HandlerKind, handlerName: string) {
    super(`${kind} handler ${JSON.stringify(handlerName)} is already registered`);
    this.kind = kind;
    this.handlerName = handlerName;
  }
}

export class HandlerNotFoundError extends HandlerRegistryError {
  readonly kind: HandlerKind;
  readonly handlerName: string;

  constructor(kind: HandlerKind, handlerName: string) {
    super(`${kind} handler ${JSON.stringify(handlerName)} is not registered`);
    this.kind = kind;
    this.handlerName = handlerName;
  }
}
