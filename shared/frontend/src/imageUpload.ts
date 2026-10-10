export const MAX_IMAGE_BYTES = 5 * 1024 * 1024;
export const IMAGE_ACCEPT = "image/png,image/jpeg,image/gif,image/webp";
export const IMAGE_ONLY_MESSAGE = "請協助說明這張圖片。";
const IMAGE_TYPES = new Set(IMAGE_ACCEPT.split(","));
const MAX_BASE64_LENGTH = 4 * Math.ceil(MAX_IMAGE_BYTES / 3);

export function validateImageFile(file: File): void {
  if (!IMAGE_TYPES.has(file.type.toLowerCase())) throw new Error("請選擇 PNG、JPEG、GIF 或 WEBP 圖片。");
  if (file.size === 0) throw new Error("圖片檔案是空的，請重新選擇。");
  if (file.size > MAX_IMAGE_BYTES) throw new Error("請選擇 5 MB 以下的圖片。");
}

/** Mirror the backend's MIME, decoded-size and file-signature checks. */
export function validateImageDataUrl(value: unknown): string {
  if (typeof value !== "string") throw new Error("圖片格式不正確，請重新選擇。");
  const comma = value.indexOf(",");
  const header = value.slice(0, comma).toLowerCase();
  const mimeType = header.slice(5, -7);
  if (comma < 0 || !header.startsWith("data:") || !header.endsWith(";base64") || !IMAGE_TYPES.has(mimeType)) {
    throw new Error("請選擇 PNG、JPEG、GIF 或 WEBP 圖片。");
  }
  const encoded = value.slice(comma + 1);
  if (!encoded || encoded.length > MAX_BASE64_LENGTH) throw new Error("請選擇 5 MB 以下的圖片。");
  const padding = encoded.endsWith("==") ? 2 : encoded.endsWith("=") ? 1 : 0;
  if (encoded.length % 4 !== 0 || /[^A-Za-z0-9+/=]/.test(encoded)
    || encoded.indexOf("=") !== (padding ? encoded.length - padding : -1)) {
    throw new Error("圖片資料無法讀取，請重新選擇。");
  }
  let content: string;
  try { content = atob(encoded); }
  catch { throw new Error("圖片資料無法讀取，請重新選擇。"); }
  if (content.length > MAX_IMAGE_BYTES) throw new Error("請選擇 5 MB 以下的圖片。");
  const valid = mimeType === "image/png" ? content.startsWith("\x89PNG\r\n\x1a\n")
    : mimeType === "image/jpeg" ? content.startsWith("\xff\xd8\xff")
    : mimeType === "image/gif" ? content.startsWith("GIF87a") || content.startsWith("GIF89a")
    : content.length >= 12 && content.startsWith("RIFF") && content.slice(8, 12) === "WEBP";
  if (!valid) throw new Error("圖片內容與檔案格式不符，請重新選擇。");
  return value;
}
