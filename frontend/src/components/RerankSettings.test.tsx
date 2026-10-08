import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { getRerankModels } from "../services/api";
import { batchConfig } from "../test/scenarioFixture";
import RerankSettings from "./RerankSettings";
vi.mock("../services/api", async () => ({ ...await vi.importActual("../services/api"), getRerankModels: vi.fn() }));
beforeEach(() => { vi.mocked(getRerankModels).mockReset(); });
it("loads choices and edits routing and the timeout independently", async () => {
  vi.mocked(getRerankModels).mockResolvedValue({ data: [{ id: "cohere/rerank-4-fast", name: "Fast" }] });
  const onChange = vi.fn();
  render(<RerankSettings config={batchConfig} adminToken="token" onChange={onChange} />);
  await screen.findByText("Fast");
  expect(getRerankModels).toHaveBeenCalledWith("token");
  fireEvent.change(screen.getByLabelText("Provider 排序"), { target: { value: "latency" } });
  expect(onChange).toHaveBeenLastCalledWith({ openrouter_provider_sort: "latency" });
  expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("重排等待上限（秒）"), { target: { value: "2" } });
  expect(onChange).toHaveBeenLastCalledWith({ rag_rerank_timeout_seconds: 2 });
});
it("keeps the configured model editable when the catalog is unavailable", async () => {
  vi.mocked(getRerankModels).mockRejectedValue(new Error("network"));
  const onChange = vi.fn();
  render(<RerankSettings config={{ ...batchConfig, rag_rerank_model: "saved/model" }} adminToken="token" onChange={onChange} />);
  await screen.findByText(/模型清單暫時無法載入/);
  expect(screen.getByLabelText("Rerank 模型")).toHaveValue("saved/model");
  fireEvent.change(screen.getByLabelText("Rerank 模型"), { target: { value: "new/model" } });
  expect(onChange).toHaveBeenLastCalledWith({ rag_rerank_model: "new/model" });
});
