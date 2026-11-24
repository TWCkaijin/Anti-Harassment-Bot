/** Small browser helpers shared by the client and admin apps. */
import { ApiError } from "./api";

/** Human-readable text for a caught value; API errors prefer the server's detail. */
export function errorMessage(error: unknown, fallback: string): string {
  if (error instanceof ApiError) return error.detail ?? error.message;
  if (error instanceof Error) return error.message;
  return fallback;
}

/** Save a Blob or text as a file through a temporary link. */
export function downloadBlob(content: Blob | string | Uint8Array, filename: string, type = "application/octet-stream"): void {
  const blob = content instanceof Blob ? content : new Blob([typeof content === "string" ? content : new Uint8Array(content)], { type });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  // Revoking synchronously can cancel the download in some browsers.
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
