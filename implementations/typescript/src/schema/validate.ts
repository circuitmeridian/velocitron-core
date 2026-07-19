import {
  validateComposition as generatedValidateComposition,
  validateNet as generatedValidateNet,
} from "./generated/validators.js";
import {NetValidationError, type NetValidationIssue} from "./errors.js";
import {snapshotJson} from "./json.js";
import type {JsonValue, ReadonlyJsonObject} from "./types.js";

interface StandaloneValidationError {
  readonly instancePath: string;
  readonly keyword: string;
  readonly message?: string;
  readonly params: unknown;
  readonly schemaPath: string;
}

interface StandaloneValidator {
  (data: unknown): boolean;
  readonly errors?: readonly StandaloneValidationError[] | null;
}

const validateNet = generatedValidateNet as StandaloneValidator;
const validateComposition = generatedValidateComposition as StandaloneValidator;

function schemaIssues(validator: StandaloneValidator): readonly NetValidationIssue[] {
  return (validator.errors ?? []).map((error) => {
    const detailsSnapshot = snapshotJson({
      keyword: error.keyword,
      params: error.params,
      schemaPath: error.schemaPath,
    });
    let details: ReadonlyJsonObject | undefined;
    if (
      typeof detailsSnapshot.value === "object" &&
      detailsSnapshot.value !== null &&
      !Array.isArray(detailsSnapshot.value)
    ) {
      // Array.isArray does not narrow readonly arrays; the runtime guard above does.
      details = detailsSnapshot.value as ReadonlyJsonObject;
    }
    return {
      code: "schema.invalid",
      path: error.instancePath,
      message: `${error.instancePath || "/"}: ${error.message ?? "does not match the canonical schema"}`,
      ...(details === undefined ? {} : {details}),
    };
  });
}

function validateShape(input: unknown, validator: StandaloneValidator): JsonValue {
  const snapshot = snapshotJson(input);
  if (snapshot.issues.length > 0) {
    throw new NetValidationError(
      snapshot.issues.map((issue) => ({
        code: issue.code as NetValidationIssue["code"],
        path: issue.path,
        message: issue.message,
      })),
    );
  }
  if (!validator(snapshot.value)) {
    const issues = schemaIssues(validator);
    throw new NetValidationError(
      issues.length > 0
        ? issues
        : [{code: "schema.invalid", path: "", message: "Document does not match the canonical schema"}],
    );
  }
  return snapshot.value;
}

export function validateNetShape(input: unknown): JsonValue {
  return validateShape(input, validateNet);
}

export function validateCompositionDocumentShape(input: unknown): JsonValue {
  return validateShape(input, validateComposition);
}
