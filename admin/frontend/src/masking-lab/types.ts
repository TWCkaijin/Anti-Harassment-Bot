import type { Entity, EntityType } from "./config";
export interface LoadMetrics {
  modelRevision?: string;
  elapsedMs: number;
  fetchedBytes: number;
  fetches: number;
  failedFetches: number;
  adapter: Record<string, string>;
}
export interface EntitySpan { start: number; end: number; type: EntityType }
export interface Detection { entities: Entity[]; spans: EntitySpan[]; elapsedMs: number; chunks: number }
export type WorkerCommand = { id: number; operation: "load" } | { id: number; operation: "extract"; text: string };
export type WorkerEvent =
  | { id: number; kind: "progress"; progress: number; text: string }
  | { id: number; kind: "loaded"; metrics: LoadMetrics }
  | { id: number; kind: "detected"; detection: Detection }
  | { id: number; kind: "error"; code: string };
