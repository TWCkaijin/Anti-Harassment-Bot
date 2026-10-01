import type { ActionOption } from "../services/api";

// Typed questions always reserve room for unknown, declined, and free text.
export const MAX_TYPED_CHOICES = 6;
export const MAX_TYPED_PREDEFINED_OPTIONS = MAX_TYPED_CHOICES - 3;
export const MAX_LEGACY_PREDEFINED_OPTIONS = 4;

/** Keep the first distinct choices without changing the values sent to the API. */
export function limitDistinctOptions<T extends ActionOption>(options: readonly T[], maximum: number): T[] {
  const labels = new Set<string>();
  const values = new Set<string>();
  const selected: T[] = [];
  for (const option of options) {
    const label = option.label.trim();
    const value = option.value.trim();
    if (!label || !value || labels.has(label) || values.has(value)) continue;
    labels.add(label);
    values.add(value);
    selected.push(option);
    if (selected.length >= maximum) break;
  }
  return selected;
}
