import { describe, expect, it } from "vitest";
import {
  detectBinaryContent,
  isAcceptedFile,
  shouldUseMultipartUpload,
} from "./files";

describe("files", () => {
  it("accepts txt and md extensions", () => {
    expect(isAcceptedFile(new File(["a"], "notes.txt"))).toBe(true);
    expect(isAcceptedFile(new File(["a"], "notes.md"))).toBe(true);
    expect(isAcceptedFile(new File(["a"], "notes.pdf"))).toBe(false);
  });

  it("routes large files to multipart", () => {
    const small = new File(["x"], "a.txt");
    Object.defineProperty(small, "size", { value: 100 });
    const large = new File(["x"], "b.txt");
    Object.defineProperty(large, "size", { value: 600 * 1024 });
    expect(shouldUseMultipartUpload(small)).toBe(false);
    expect(shouldUseMultipartUpload(large)).toBe(true);
  });

  it("detects binary content", () => {
    expect(detectBinaryContent("hello world")).toBe(false);
    expect(detectBinaryContent("hello\x00world")).toBe(true);
  });
});
