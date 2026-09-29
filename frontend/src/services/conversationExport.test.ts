import { expect, it } from "vitest";
import { formatConversationText } from "./conversationExport";
import zhTW from "../i18n/locales/zh-TW";
import en from "../i18n/locales/en";
it.each([zhTW, en])("exports the bounded summary with version and all three statuses alongside messages", labels => {
  const text = formatConversationText({ id: "test", createdAt: 1, messages: [{ id: "m", role: "user", content: "請說明", timestamp: 1 }], caseFacts: { schema_version: 1, revision: 4, facts: { other_role: { status: "provided", value: "主管" }, city: { status: "declined" }, event_time: { status: "unknown" } } } }, labels);
  expect(text).toContain("schema_version: 1, revision: 4");
  expect(text).toContain(`${labels.caseFactLabels.other_role} [provided]: 主管`);
  expect(text).toContain(`${labels.caseFactLabels.city} [declined]: ${labels.caseFactDeclined}`);
  expect(text).toContain(`${labels.caseFactLabels.event_time} [unknown]: ${labels.caseFactUnknown}`);
  expect(text).toContain("[User]: 請說明");
});
it("exports old conversations without inventing confirmed details", () => {
  expect(formatConversationText({ id: "legacy", createdAt: 1, messages: [{ id: "m", role: "user", content: "我是老師", timestamp: 1 }] }, zhTW)).toContain(zhTW.caseSummaryEmpty);
});
