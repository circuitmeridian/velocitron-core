export class EngineConfigurationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = new.target.name;
  }
}

export class UnknownTransitionError extends Error {
  readonly net: string;
  readonly transition: string;

  constructor(net: string, transition: string) {
    super(`net ${JSON.stringify(net)} has no transition named ${JSON.stringify(transition)}`);
    this.name = new.target.name;
    this.net = net;
    this.transition = transition;
  }
}

export class DepositViolationError extends Error {
  readonly transition: string;

  constructor(transition: string, detail?: string) {
    super(detail ?? `transition ${JSON.stringify(transition)} produced tokens that violate its produce contract`);
    this.name = new.target.name;
    this.transition = transition;
  }
}

export class TokenInjectionError extends Error {
  readonly net: string;
  readonly place: string;

  constructor(net: string, place: string, message: string) {
    super(`token injection: ${message}`);
    this.name = new.target.name;
    this.net = net;
    this.place = place;
  }
}
