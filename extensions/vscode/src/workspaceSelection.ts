export interface WorkspaceChoice<T> {
  readonly label: string;
  readonly description: string;
  readonly folder: T;
}

export type WorkspaceChoicePicker<T> = (
  choices: readonly WorkspaceChoice<T>[]
) => PromiseLike<WorkspaceChoice<T> | undefined>;

export async function chooseWorkspace<T>(
  choices: readonly WorkspaceChoice<T>[],
  picker: WorkspaceChoicePicker<T>
): Promise<T | undefined> {
  if (choices.length === 0) return undefined;
  if (choices.length === 1) return choices[0]?.folder;
  const picked = await picker(choices);
  return picked && choices.includes(picked) ? picked.folder : undefined;
}
