import { describe, expect, it } from "vitest";
import { formatApiError } from "./errors";

describe("formatApiError", () => {
  it("returns string detail as-is", () => {
    expect(formatApiError("topic is required")).toBe("topic is required");
  });

  it("formats FastAPI validation array", () => {
    const detail = [
      { loc: ["body", "topic"], msg: "Field required", type: "missing" },
    ];
    expect(formatApiError(detail)).toBe("body.topic: Field required");
  });

  it("joins multiple validation errors", () => {
    const detail = [
      { loc: ["body", "topic"], msg: "Field required" },
      { loc: ["body", "text"], msg: "Field required" },
    ];
    expect(formatApiError(detail)).toContain("body.topic");
    expect(formatApiError(detail)).toContain("body.text");
  });
});
