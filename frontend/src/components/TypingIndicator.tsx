/**
 * TypingIndicator — AI 思考中的打字動畫
 * 三個跳動的圓點，搭配暖色系 AI 頭像。
 */
import MaterialIcon from "./MaterialIcon";

interface TypingIndicatorProps {
  message?: string | null;
}

export default function TypingIndicator({ message = "正在回覆中" }: TypingIndicatorProps) {
  return (
    <div className="flex gap-3 justify-start animate-fade-in-up">
      <div className="shrink-0 w-8 h-8 rounded-full bg-primary flex items-center justify-center mt-1 pulse-glow">
        <MaterialIcon icon="shield" size={16} filled className="text-on-primary" />
      </div>
      <div className="glass-morphism rounded-[24px] rounded-bl-md px-5 py-3.5">
        <div className="flex items-center gap-2">
          <span className="typing-dot" />
          <span className="typing-dot" />
          <span className="typing-dot" />
          <span className="ml-2 text-xs text-on-surface/65">{message}</span>
        </div>
      </div>
    </div>
  );
}
