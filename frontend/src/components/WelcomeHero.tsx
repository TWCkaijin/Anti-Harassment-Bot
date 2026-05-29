/**
 * WelcomeHero — Stitch 風格 Hero Section
 * Mesh gradient 背景 + AI Ready 提示 chip + 大字標題 + Gemini 漸變輸入框 + 建議 Chips。
 */
import { useRef, useState, type KeyboardEvent, type ChangeEvent } from "react";
import MaterialIcon from "./MaterialIcon";
import { useI18n } from "../i18n";

interface WelcomeHeroProps {
  onSuggest: (message: string, imageBase64?: string, imageUrl?: string) => void;
}

export default function WelcomeHero({ onSuggest }: WelcomeHeroProps) {
  const { t } = useI18n();
  const [inputValue, setInputValue] = useState("");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  
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
    const trimmed = inputValue.trim();
    if (!trimmed && !selectedFile) return;

    let base64: string | undefined;
    if (selectedFile) {
      base64 = await fileToBase64(selectedFile);
    }

    onSuggest(trimmed, base64, previewUrl || undefined);
    
    setInputValue("");
    removeFile();
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.nativeEvent.isComposing || e.keyCode === 229) return;
    
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const suggestions = [t.suggestLaw, t.suggestReport, t.suggestSelfCare];

  return (
    <div className="flex-1 flex flex-col hero-mesh-gradient overflow-y-auto">
      <section className="w-full px-5 lg:px-10 py-12 lg:py-24 flex flex-col items-center text-center flex-1 justify-center">
        {/* AI Ready Chip */}
        <div className="mb-8 inline-flex items-center gap-2 px-4 py-2 bg-secondary-container/20 text-secondary border border-secondary/20 rounded-full animate-pulse">
          <MaterialIcon icon="auto_awesome" size={18} />
          <span className="text-xs font-semibold tracking-wide">
            {t.heroChip}
          </span>
        </div>

        {/* 大字標題 */}
        <h2 className="text-3xl lg:text-4xl font-bold tracking-tight text-on-surface max-w-4xl mb-6 leading-tight">
          {t.heroTitle}
          <br />
          <span className="text-primary">{t.heroTitleHighlight}</span>
        </h2>

        <p className="text-sm lg:text-base text-on-surface-variant mb-10 max-w-2xl leading-relaxed">
          {t.heroDesc}
        </p>

        {/* Stitch v0.1 輸入框 */}
        <div className="w-full relative mt-4">
          {previewUrl && (
            <div className="mb-3 relative inline-block animate-fade-in text-left">
              <img src={previewUrl} alt="Preview" className="h-20 w-auto rounded-lg object-cover border border-outline/20 shadow-sm" />
              <button 
                onClick={removeFile}
                className="absolute -top-2 -right-2 bg-surface text-on-surface hover:text-error rounded-full shadow-md p-1 border border-outline/10 transition-colors"
              >
                <MaterialIcon icon="close" size={16} />
              </button>
            </div>
          )}
          
          <div 
            onClick={() => textareaRef.current?.focus()}
            className="relative bg-white border border-primary/30 rounded-3xl shadow-lg flex items-end px-4 lg:px-6 py-2 transition-shadow focus-within:shadow-float focus-within:border-primary/50 text-left cursor-text"
          >
            {/* 左側視覺裝飾 Icon */}
            <button 
              className="p-2 text-on-surface/40 hover:text-primary transition-colors cursor-pointer mb-0.5 shrink-0" 
              onClick={(e) => {
                e.stopPropagation();
                fileInputRef.current?.click();
              }}
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
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={t.heroInputPlaceholder}
              rows={1}
              className="flex-1 bg-transparent border-none focus:ring-0 px-2 lg:px-4 py-2.5 text-on-surface placeholder:text-on-surface/30 font-medium resize-none leading-relaxed"
              style={{ maxHeight: "160px" }}
            />
            <button
              onClick={handleSend}
              disabled={!inputValue.trim() && !selectedFile}
              className={`
                w-10 h-10 lg:w-12 lg:h-12 rounded-full flex items-center justify-center text-white transition-all group shrink-0 mb-0.5 cursor-pointer
                ${
                  (inputValue.trim().length > 0 || selectedFile)
                    ? "bg-primary hover:bg-primary/90 shadow-md"
                    : "bg-primary/40 cursor-not-allowed"
                }
              `}
            >
              <MaterialIcon 
                icon="arrow_forward" 
                size={24} 
                className={(inputValue.trim().length > 0 || selectedFile) ? "group-hover:translate-x-0.5 transition-transform" : ""}
              />
            </button>
          </div>

          {/* 建議 Chips */}
          <div className="mt-4 flex flex-wrap justify-center gap-2">
            {suggestions.map((suggestion) => (
              <button
                key={suggestion}
                onClick={() => onSuggest(suggestion)}
                className="px-3 py-1.5 bg-surface-container rounded-full text-xs font-semibold text-on-surface-variant cursor-pointer hover:bg-surface-container-high transition-colors tracking-wide"
              >
                {suggestion}
              </button>
            ))}
          </div>
        </div>
      </section>
    </div>
  );
}
