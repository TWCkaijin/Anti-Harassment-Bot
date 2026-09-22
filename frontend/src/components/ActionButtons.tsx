import type { ActionButton } from "../services/api";
import { getSafeResourceActions } from "./actionButtonValidation";
import MaterialIcon from "./MaterialIcon";

interface ActionButtonsProps {
  actions: ActionButton[];
}

const buttonClassName = "inline-flex min-h-10 max-w-full items-center gap-2 rounded-lg border border-secondary/20 bg-white px-3 py-2 text-sm font-medium text-secondary transition-colors hover:bg-secondary-container/30 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-secondary";

/** Resource actions share the follow-up panel; choice actions render inline there. */
export default function ActionButtons({ actions }: ActionButtonsProps) {
  const resourceActions = getSafeResourceActions(actions);
  if (resourceActions.length === 0) return null;

  return (
    <div className="flex flex-wrap gap-2" aria-label="相關資源">
      {resourceActions.map((action, index) => (
        <a
          key={`${action.action}-${index}`}
          href={action.action === "tel" ? `tel:${action.phone_number}` : action.url}
          target={action.action === "url" ? "_blank" : undefined}
          rel={action.action === "url" ? "noopener noreferrer" : undefined}
          className={buttonClassName}
        >
          <span aria-hidden="true"><MaterialIcon icon={action.action === "tel" ? "call" : "open_in_new"} size={17} /></span>
          <span className="min-w-0 break-words">{action.label}</span>
          {action.action === "url" && <span className="sr-only">（另開新分頁）</span>}
        </a>
      ))}
    </div>
  );
}
