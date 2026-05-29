/**
 * ChatInput — 底部輸入列 (Stitch 新版對話內頁 v0.1)
 * 白底藥丸形狀 + 淡橘色邊框 + 掃描圖示 + 動態發送按鈕。
 */
import { useRef, useState, type KeyboardEvent, type ChangeEvent } from "react";
import MaterialIcon from "./MaterialIcon";
import { useI18n } from "../i18n";

interface ChatInputProps {
  onSend: (message: string, imageBase64?: string, imageUrl?: string) => void;
  isLoading?: boolean;
}

export default function ChatInput({ onSend, isLoading }: ChatInputProps) {
  const { t } = useI18n();
  const [value, setValue] = useState("");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [isFocused, setIsFocused] = useState(false);
  
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleFileChange = (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      if (file.size > 5 * 1024 * 1024) {
        alert("圖片大小不能超過 5MB");
        return;
      }
      setSelectedFile(file);
      const url = URL.createObjectURL(file);
      setPreviewUrl(url);
    }
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
          className={`relative bg-white border rounded-3xl shadow-lg flex items-end px-4 lg:px-6 py-2 transition-all cursor-text ${isFocused ? "border-primary/50" : "border-primary/30"}`}
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
            onInput={handleInput}
            placeholder={t.inputPlaceholder}
            rows={1}
            disabled={isLoading}
            className="flex-1 bg-transparent border-none focus:ring-0 px-2 lg:px-4 py-2.5 text-on-surface placeholder:text-on-surface/30 font-medium resize-none leading-relaxed disabled:opacity-50 disabled:cursor-not-allowed"
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
