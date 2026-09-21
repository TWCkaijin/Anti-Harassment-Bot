import { afterEach, describe, expect, it, vi } from "vitest";

import {
  getRuntimeConfig,
  normalizeApiErrorDetail,
  sendChat,
  updateRuntimeConfig,
  type RuntimeConfig,
} from "./api";

const runtimeConfig: RuntimeConfig = {
  openrouter_model: "test/model",
  rag_retrieval_top_k: 3,
  rag_distance_threshold: null,
  enable_anonymization: true,
  temperature: 0.2,
  top_p: 1,
  max_tokens: 1200,
  reasoning_effort: "none",
  agent_prompt_sections: {},
  rag_collections: { law: "laws", judgment: "judgments", remedy: "remedies" },
  maintenance_message: "",
  enable_image_upload: true,
  development_mode: false,
  source: "firestore",
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("normalizeApiErrorDetail", () => {
  it("formats the current field/message validation envelope", () => {
    expect(normalizeApiErrorDetail([
      {
        field: "message",
        message: "String should have at most 2000 characters",
        type: "string_too_long",
      },
    ])).toBe("message: String should have at most 2000 characters");
  });

  it("keeps compatibility with legacy Pydantic loc/msg issues", () => {
    expect(normalizeApiErrorDetail([
      {
        loc: ["body", "message"],
        msg: "String should have at least 1 character",
        type: "string_too_short",
      },
    ])).toBe("body.message: String should have at least 1 character");
  });
});

describe("sendChat", () => {
  it("exposes a normalized detail on 422 responses", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      detail: "Invalid request payload",
      errors: [{ field: "message", message: "Field required", type: "missing" }],
      retryable: false,
    }), {
      status: 422,
      headers: { "Content-Type": "application/json" },
    })));

    const request = sendChat({ message: "test", history: [], use_rag: true });

    await expect(request).rejects.toMatchObject({
      status: 422,
      detail: "Invalid request payload: message: Field required",
      retryable: false,
    });
  });
});

describe("runtime config API", () => {
  it("loads config with the verified bearer token", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(runtimeConfig), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(getRuntimeConfig("admin-token")).resolves.toEqual(runtimeConfig);
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/v1/admin/config"),
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: "Bearer admin-token" }),
      }),
    );
  });

  it("normalizes config validation errors on save", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      detail: "Invalid request payload",
      errors: [{
        field: "rag_distance_threshold",
        message: "Must be between 0 and 2",
        type: "value_error",
      }],
      retryable: false,
    }), {
      status: 422,
      headers: { "Content-Type": "application/json" },
    })));

    const request = updateRuntimeConfig("admin-token", { rag_distance_threshold: 3 });

    await expect(request).rejects.toMatchObject({
      status: 422,
      detail: "Invalid request payload: rag_distance_threshold: Must be between 0 and 2",
    });
  });
});
