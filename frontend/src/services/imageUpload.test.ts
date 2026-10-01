import { describe, expect, it } from "vitest";
import { MAX_IMAGE_BYTES, validateImageDataUrl, validateImageFile } from "./imageUpload";

const png = "\x89PNG\r\n\x1a\n";
const image = (type: string, content: string) => `data:${type};base64,${btoa(content)}`;

describe("image upload boundaries", () => {
  it.each([
    ["image/png", png], ["image/jpeg", "\xff\xd8\xff"],
    ["image/gif", "GIF87a"], ["image/gif", "GIF89a"], ["image/webp", "RIFF1234WEBP"],
  ])("accepts matching %s signatures without changing the data URL", (type, content) => {
    const value = image(type, content);
    expect(validateImageDataUrl(value)).toBe(value);
  });
  it("accepts exactly 5 MiB and rejects one extra decoded byte", () => {
    const exact = png + "\0".repeat(MAX_IMAGE_BYTES - png.length);
    expect(validateImageDataUrl(image("image/png", exact))).toBe(image("image/png", exact));
    expect(() => validateImageDataUrl(image("image/png", `${exact}\0`))).toThrow(/5 MB/);
  });
  it.each([
    null, undefined, {}, "https://example.com/image.png", "data:image/svg+xml;base64,PHN2Zz4=",
    "data:image/png;base64,", "data:image/png;base64,aGVsbG8=", "data:image/png;base64,@@@@",
    "data:image/png;base64,AAAA=", "data:image/png;base64,AA=A", "data:image/png;base64,AA==\n",
    "data:image/webp;base64,UklGRg==", "data:image/jpeg;base64,iVBORw0KGgo=",
  ])("rejects malformed, unsupported, or MIME-mismatched image input: %j", value => {
    expect(() => validateImageDataUrl(value)).toThrow();
  });
  it("rejects unsupported files and large files before reading their contents", () => {
    expect(() => validateImageFile(new File(["x"], "image.svg", { type: "image/svg+xml" }))).toThrow(/PNG/);
    expect(() => validateImageFile(new File([], "empty.png", { type: "image/png" }))).toThrow(/空/);
    const file = new File(["x"], "large.png", { type: "image/png" });
    Object.defineProperty(file, "size", { value: MAX_IMAGE_BYTES + 1 });
    expect(() => validateImageFile(file)).toThrow(/5 MB/);
  });
});
