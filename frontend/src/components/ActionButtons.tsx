import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";

import type { ActionButton, OptionsActionButton } from "../services/api";
import MaterialIcon from "./MaterialIcon";

interface ActionButtonsProps {
  actions: ActionButton[];
  isLoading?: boolean;
  onSend?: (message: string) => void;
}

const buttonClassName = "inline-flex min-h-10 items-center gap-2 rounded-lg bg-secondary px-4 py-2 text-sm font-bold text-white shadow-md transition-colors hover:bg-secondary/90 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-secondary disabled:cursor-not-allowed disabled:opacity-40";

function hasText(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

function boundedText(value: unknown, maximum: number): value is string {
  return hasText(value) && [...value].length <= maximum;
}

function safeUrl(value: unknown): string | null {
  if (!hasText(value) || value.length > 2048 || !/^https?:\/\/[^/]/i.test(value) || /[\s\\]/.test(value)
    || [...value].some((character) => character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127)) return null;
  try {
    const url = new URL(value);
    if (!["https:", "http:"].includes(url.protocol) || !url.hostname || url.username || url.password) return null;
    return url.href;
  } catch {
    return null;
  }
}

function safePhone(value: unknown): string | null {
  return hasText(value) && /^[0-9+()-]{3,24}$/.test(value) ? value : null;
}

// Stored conversations may predate the current schema; validate before rendering.
function isValidOptions(action: OptionsActionButton): boolean {
  return hasText(action.id) && /^[a-z][a-z0-9_-]{1,63}$/.test(action.id)
    && boundedText(action.title, 160) && Array.isArray(action.options)
    && action.options.length >= 2 && action.options.length <= 8
    && action.options.every((option) => option && boundedText(option.label, 80)
      && boundedText(option.value, 500))
    && new Set(action.options.map((option) => option.value.trim())).size === action.options.length;
}

interface OptionsDialogProps {
  action: OptionsActionButton;
  isLoading: boolean;
  onSelect: (value: string) => void;
  onClose: () => void;
}

function OptionsDialog({ action, isLoading, onSelect, onClose }: OptionsDialogProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const descriptionId = useId();
  const submitted = useRef(false);

  useEffect(() => {
    const dialog = dialogRef.current;
    const previousFocus = document.activeElement;
    if (!dialog) return;
    // Native modal dialogs keep keyboard focus inside and make the page inert.
    dialog.showModal();
    dialog.querySelector<HTMLButtonElement>("button:not(:disabled)")?.focus();
    return () => {
      dialog.close();
      if (previousFocus instanceof HTMLElement && previousFocus.isConnected) previousFocus.focus();
    };
  }, []);

  return createPortal(
    <dialog
      ref={dialogRef}
      aria-labelledby={titleId}
      aria-describedby={descriptionId}
      aria-modal="true"
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
      className="fixed inset-0 m-auto max-h-[85dvh] w-[calc(100%_-_2rem)] max-w-md overflow-y-auto rounded-2xl border border-outline/15 bg-white p-0 text-on-surface shadow-xl backdrop:bg-black/40"
    >
      <div className="p-5 sm:p-6">
        <h2 id={titleId} className="text-lg font-bold">{action.title}</h2>
        <p id={descriptionId} className="mt-2 text-sm text-on-surface/65">選擇一個選項後，會將內容傳送至對話。</p>
        <div className="mt-5 flex flex-col gap-2">
          {action.options.map((option, index) => (
            <button
              key={`${index}-${option.value}`}
              type="button"
              disabled={isLoading}
              onClick={() => {
                if (isLoading || submitted.current) return;
                submitted.current = true;
                onSelect(option.value);
              }}
              className="min-h-11 rounded-lg border border-secondary/20 bg-secondary-container/20 px-4 py-3 text-left text-sm font-semibold text-secondary transition-colors hover:bg-secondary-container/40 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-secondary disabled:cursor-not-allowed disabled:opacity-40"
            >
              {option.label}
            </button>
          ))}
        </div>
        <button
          type="button"
          onClick={onClose}
          className="mt-5 min-h-10 w-full rounded-lg border border-outline/25 px-4 py-2 text-sm font-semibold hover:bg-surface-container-low focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-secondary"
        >
          取消
        </button>
      </div>
    </dialog>,
    document.body,
  );
}

export default function ActionButtons({ actions, isLoading = false, onSend }: ActionButtonsProps) {
  const [activeOptions, setActiveOptions] = useState<OptionsActionButton | null>(null);

  return (
    <div className="flex flex-wrap gap-2 pt-2">
      {actions.map((action, index) => {
        if (!action || !boundedText(action.label, 80)) return null;
        if (action.action === "tel") {
          const phone = safePhone(action.phone_number);
          return phone ? (
            <a key={`tel-${index}`} href={`tel:${phone}`} className={buttonClassName}>
              <span aria-hidden="true"><MaterialIcon icon="call" size={18} /></span>
              <span>{action.label}</span>
            </a>
          ) : null;
        }
        if (action.action === "url") {
          const url = safeUrl(action.url);
          return url ? (
            <a key={`url-${index}`} href={url} target="_blank" rel="noopener noreferrer" className={buttonClassName}>
              <span aria-hidden="true"><MaterialIcon icon="open_in_new" size={18} /></span>
              <span>{action.label}</span>
              <span className="sr-only">（另開新分頁）</span>
            </a>
          ) : null;
        }
        if (action.action === "options" && isValidOptions(action)) {
          return (
            <button
              key={`options-${index}-${action.id}`}
              type="button"
              aria-haspopup="dialog"
              disabled={isLoading || !onSend}
              onClick={() => setActiveOptions(action)}
              className={buttonClassName}
            >
              <span aria-hidden="true"><MaterialIcon icon="list" size={18} /></span>
              <span>{action.label}</span>
            </button>
          );
        }
        return null;
      })}
      {activeOptions && (
        <OptionsDialog
          action={activeOptions}
          isLoading={isLoading || !onSend}
          onClose={() => setActiveOptions(null)}
          onSelect={(value) => {
            if (isLoading || !onSend) return;
            setActiveOptions(null);
            onSend(value);
          }}
        />
      )}
    </div>
  );
}
