export type SendOutcome = void | boolean | Promise<void | boolean>;

/** UI drafts are cleared only after their request was accepted for sending. */
export function settleSendOutcome(result: SendOutcome, accepted: () => void, cancelled: () => void = () => {}): void {
  const finish = (value: void | boolean) => value === false ? cancelled() : accepted();
  if (typeof result === "object" && result !== null) void result.then(finish, cancelled);
  else finish(result);
}
