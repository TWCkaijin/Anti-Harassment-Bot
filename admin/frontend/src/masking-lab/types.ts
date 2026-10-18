import type { Entity } from "./config";
import type { ExtractionRound } from "./pipeline";
export interface LoadMetrics {
  elapsedMs: number;
  fetchedBytes: number;
  fetches: number;
  failedFetches: number;
  adapter: Record<string, string>;
}
export interface Detection { entities: Entity[]; elapsedMs: number; chunks: number; rounds?: ExtractionRound[] }
export type WorkerCommand = { id: number; operation: "load" } | { id: number; operation: "extract"; text: string };
export type WorkerEvent =
  | { id: number; kind: "progress"; progress: number; text: string }
  | { id: number; kind: "loaded"; metrics: LoadMetrics }
  | { id: number; kind: "detected"; detection: Detection }
  | { id: number; kind: "error"; code: string; rounds?: ExtractionRound[] };
