import type { ScenarioSkill } from "../services/api";
import { MaterialIcon } from "@rag/shared/chat-ui";
import SkillActionsEditor from "./SkillActionsEditor";
import { Field, Toggle } from "./SettingsFields";

export default function SkillsSettings({
  skills,
  selectedSkill,
  onSelect,
  onCreate,
  onChange,
}: {
  skills: ScenarioSkill[];
  selectedSkill: ScenarioSkill | null;
  onSelect: (id: string) => void;
  onCreate: () => void;
  onChange: (changes: Partial<ScenarioSkill>) => void;
}) {
  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-5 md:flex-row">
      <aside className="w-full shrink-0 border-b border-outline/10 pb-4 md:w-48 md:border-r md:border-b-0 md:pr-4 md:pb-0">
        <div className="mb-3 flex items-center justify-between">
          <h3 className="font-bold">Skills</h3>
          <button
            onClick={onCreate}
            className="rounded-md p-1 text-primary hover:bg-primary-container"
            aria-label="新增 Skill"
          >
            <MaterialIcon icon="add" size={18} />
          </button>
        </div>
        <p className="mb-3 text-xs leading-5 text-on-surface/55">只作用於目前環境。</p>
        <div className="flex gap-1 overflow-x-auto md:block md:space-y-1">
          {skills.map((skill) => (
            <button
              key={skill.id}
              onClick={() => onSelect(skill.id)}
              className={`w-40 shrink-0 rounded-lg px-3 py-2 text-left text-sm md:w-full ${
                selectedSkill?.id === skill.id
                  ? "bg-primary-container text-primary"
                  : "hover:bg-surface-container"
              }`}
            >
              <span className="block truncate font-semibold">{skill.name}</span>
              <span className="text-[0.6875rem] opacity-65">
                {skill.enabled ? "啟用" : "停用"} · {skill.priority}
              </span>
            </button>
          ))}
        </div>
      </aside>
      {selectedSkill ? (
        <div className="min-w-0 flex-1 space-y-5">
          <div>
            <h3 className="text-xl font-bold">{selectedSkill.name}</h3>
            <p className="mt-1 text-sm text-on-surface/60">
              觸發後會在同一次模型呼叫注入情境指令與可用按鈕。
            </p>
          </div>
          <Field label="Skill ID">
            <input value={selectedSkill.id} disabled className="input cursor-not-allowed opacity-55" />
          </Field>
          <div className="grid gap-3 sm:grid-cols-[1fr_140px]">
            <Field label="名稱">
              <input
                value={selectedSkill.name}
                onChange={(event) => onChange({ name: event.target.value })}
                className="input"
              />
            </Field>
            <Field label="優先順序">
              <input
                type="number"
                value={selectedSkill.priority}
                onChange={(event) => onChange({ priority: Number(event.target.value) })}
                className="input"
              />
            </Field>
          </div>
          <Toggle
            label="啟用此 Skill"
            checked={selectedSkill.enabled}
            onChange={(enabled) => onChange({ enabled })}
          />
          <Field label="觸發詞（以逗號分隔）">
            <input
              value={selectedSkill.trigger_keywords.join(", ")}
              onChange={(event) =>
                onChange({
                  trigger_keywords: event.target.value
                    .split(",")
                    .map((value) => value.trim())
                    .filter(Boolean),
                })
              }
              className="input"
              placeholder="請輸入此情境的關鍵詞"
            />
          </Field>
          <Field label="情境指令">
            <textarea
              value={selectedSkill.instruction}
              onChange={(event) => onChange({ instruction: event.target.value })}
              rows={8}
              className="input resize-y leading-6"
              placeholder="說明何時使用下方按鈕、選擇哪些按鈕，以及如何回應使用者"
            />
          </Field>
          <SkillActionsEditor
            key={selectedSkill.id}
            actions={selectedSkill.actions}
            onChange={(actions) => onChange({ actions })}
          />
        </div>
      ) : (
        <div className="flex flex-1 items-center justify-center text-sm text-on-surface/55">
          選擇或新增一個 Skill
        </div>
      )}
    </div>
  );
}
