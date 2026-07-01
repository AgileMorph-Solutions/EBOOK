type FastAPIValidationError = {
  loc?: (string | number)[];
  msg?: string;
  type?: string;
};

export function formatApiError(detail: unknown): string {
  if (typeof detail === "string") {
    return detail;
  }
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => {
        const err = item as FastAPIValidationError;
        if (!err.msg) return null;
        const field = err.loc?.filter((p) => typeof p === "string").join(".") ?? "field";
        return `${field}: ${err.msg}`;
      })
      .filter(Boolean);
    if (messages.length > 0) {
      return messages.join("; ");
    }
  }
  if (detail && typeof detail === "object") {
    return JSON.stringify(detail);
  }
  return "Request failed";
}
