import { useEffect, useRef, useState } from "react";
import { validateImageDataUrl, validateImageFile } from "../services/imageUpload";

export interface ImageAttachment { file: File; dataUrl: string }

/** A selected image stays local until the composer sends it. */
export function useImageAttachment() {
  const [attachment, setAttachment] = useState<ImageAttachment | null>(null);
  const [isReading, setIsReading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const readerRef = useRef<FileReader | null>(null);
  useEffect(() => () => {
    const reader = readerRef.current;
    readerRef.current = null;
    reader?.abort();
  }, []);

  const selectFile = (file: File) => {
    try { validateImageFile(file); }
    catch (failure) { setError(failure instanceof Error ? failure.message : "無法讀取圖片。"); return; }
    const previous = readerRef.current;
    readerRef.current = null;
    previous?.abort();
    const reader = new FileReader();
    readerRef.current = reader;
    setError(null);
    setIsReading(true);
    const finish = () => { if (readerRef.current === reader) { readerRef.current = null; setIsReading(false); } };
    reader.onload = () => {
      if (readerRef.current !== reader) return;
      try { setAttachment({ file, dataUrl: validateImageDataUrl(reader.result) }); }
      catch (failure) { setError(failure instanceof Error ? failure.message : "無法讀取圖片。"); }
      finish();
    };
    reader.onerror = () => { if (readerRef.current === reader) { setError("無法讀取圖片，請重新選擇。"); finish(); } };
    reader.onabort = finish;
    try { reader.readAsDataURL(file); }
    catch { if (readerRef.current === reader) { setError("無法讀取圖片，請重新選擇。"); finish(); } }
  };

  const removeImage = (expected?: ImageAttachment) => {
    const reader = readerRef.current;
    readerRef.current = null;
    reader?.abort();
    setIsReading(false);
    setError(null);
    setAttachment(current => expected && current !== expected ? current : null);
  };
  return { attachment, isReading, error, selectFile, removeImage, setError };
}
