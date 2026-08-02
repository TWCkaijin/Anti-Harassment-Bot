/**
 * ChatInput — 底部輸入列 (Stitch 新版對話內頁 v0.1)
 * 白底藥丸形狀 + 淡橘色邊框 + 掃描圖示 + 動態發送按鈕。
 */
import {
  useRef,
  useState,
  type ChangeEvent,
  type ClipboardEvent,
  type KeyboardEvent,
} from "react";
import MaterialIcon from "./MaterialIcon";
import { useI18n } from "../i18n";

interface ChatInputProps {
  onSend: (message: string, imageBase64?: string, imageUrl?: string) => void;
  isLoading?: boolean;
  suggestedReplies?: string[];
}

export default function ChatInput({ onSend, isLoading, suggestedReplies = [] }: ChatInputProps) {
  const { t } = useI18n();
  const [value, setValue] = useState("");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [isFocused, setIsFocused] = useState(false);
  
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const selectImage = (file: File) => {
    if (!file.type.startsWith("image/")) return;
    if (file.size > 5 * 1024 * 1024) {
      alert("圖片大小不能超過 5MB");
      return;
    }
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    setSelectedFile(file);
    setPreviewUrl(URL.createObjectURL(file));
  };

  const handleFileChange = (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) selectImage(file);
  };

  const removeFile = () => {
    setSelectedFile(null);
    if (previewUrl) {
      URL.revokeObjectURL(previewUrl);
      setPreviewUrl(null);
    }
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  };

  const fileToBase64 = (file: File): Promise<string> => {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.readAsDataURL(file);
      reader.onload = () => resolve(reader.result as string);
      reader.onerror = (error) => reject(error);
    });
  };

  const handleSend = async () => {
    if (isLoading) return;
    const trimmed = value.trim();
    if (!trimmed && !selectedFile) return;

    let base64: string | undefined;
    if (selectedFile) {
      base64 = await fileToBase64(selectedFile);
    }

    onSend(trimmed, base64, previewUrl || undefined);
    
    setValue("");
    removeFile();
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
    }
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.nativeEvent.isComposing || e.keyCode === 229) return;
    
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handlePaste = (event: ClipboardEvent<HTMLTextAreaElement>) => {
    const image = Array.from(event.clipboardData.files).find((file) =>
      file.type.startsWith("image/")
    );
    if (image) selectImage(image);
  };

  const handleInput = () => {
    const el = textareaRef.current;
    if (el) {
      el.style.height = "auto";
      el.style.height = Math.min(el.scrollHeight, 160) + "px";
    }
  };

  const hasContent = value.trim().length > 0 || selectedFile !== null;

  return (
    <footer className="px-6 lg:px-10 pb-6 lg:pb-10 bg-transparent">
      <div className="w-full relative">
        {suggestedReplies.length > 0 && (
          <section className="mb-3" aria-label="建議提問">
            <p className="mb-2 text-xs font-semibold text-on-surface/60">建議提問</p>
            <div className="flex flex-wrap gap-2">
              {suggestedReplies.map((suggestion) => (
                <button
                  key={suggestion}
                  type="button"
                  onClick={() => onSend(suggestion)}
                  disabled={isLoading}
                  className="max-w-full rounded-lg border border-primary/20 bg-white px-3 py-2 text-left text-xs font-medium text-primary shadow-sm transition-colors hover:bg-primary-container disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {suggestion}
                </button>
              ))}
            </div>
          </section>
        )}

        {previewUrl && (
          <div className="mb-3 relative inline-block">
            <img src={previewUrl} alt="Preview" className="h-20 w-auto rounded-lg object-cover border border-primary/20 shadow-sm" />
            <button 
              onClick={removeFile}
              className="absolute -top-2 -right-2 bg-white text-on-surface hover:text-error rounded-full shadow-md p-1 border border-primary/10 transition-colors"
            >
              <MaterialIcon icon="close" size={16} />
            </button>
          </div>
        )}

        <div 
          onClick={() => textareaRef.current?.focus()}
          onFocus={() => setIsFocused(true)}
          onBlur={() => setIsFocused(false)}
          className={`relative bg-white border rounded-[32px] shadow-lg flex items-end px-4 lg:px-6 py-2 transition-all cursor-text ${isFocused ? "border-primary ring-2 ring-primary/20" : "border-primary/30"}`}
        >
          <button 
            className="p-2 text-on-surface/40 hover:text-primary transition-colors cursor-pointer mb-0.5 shrink-0" 
            onClick={() => fileInputRef.current?.click()}
          >
            <MaterialIcon icon="image" size={24} />
          </button>
          <input 
            type="file" 
            accept="image/*" 
            className="hidden" 
            ref={fileInputRef} 
            onChange={handleFileChange} 
          />

          <textarea
            ref={textareaRef}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={handleKeyDown}
            onPaste={handlePaste}
            onInput={handleInput}
            placeholder={t.inputPlaceholder}
            rows={1}
            className="flex-1 bg-transparent border-none focus:border-none focus:ring-0 outline-none focus:outline-none px-2 lg:px-4 py-2.5 text-on-surface placeholder:text-on-surface/30 font-medium resize-none leading-relaxed"
            style={{ maxHeight: "160px" }}
          />

          <button
            onClick={handleSend}
            disabled={!hasContent || isLoading}
            className={`
              w-10 h-10 lg:w-12 lg:h-12 rounded-full flex items-center justify-center text-white transition-all group shrink-0 mb-0.5 cursor-pointer
              ${
                hasContent && !isLoading
                  ? "bg-primary hover:bg-primary/90 shadow-md"
                  : "bg-primary/40 cursor-not-allowed"
              }
            `}
            aria-label={t.sendMessage}
          >
            <MaterialIcon
              icon="arrow_forward"
              size={24}
              className={hasContent && !isLoading ? "group-hover:translate-x-0.5 transition-transform" : ""}
            />
          </button>
        </div>

        {/* 底部免責聲明 */}
        <div className="text-center mt-4">
          <p className="text-[12px] text-on-surface/40">
            {t.aiDisclaimer}{" "}
            <span className="text-primary font-bold cursor-pointer hover:underline">
              {t.hotline113}
            </span>
          </p>
        </div>
      </div>
    </footer>
  );
}
