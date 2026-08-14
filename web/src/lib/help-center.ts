export type HelpCategory = "gettingStarted" | "create" | "collaborate";

const CREATE_TUTORIALS = new Set(["upload", "contribute", "qcm"]);
const COLLABORATION_TUTORIALS = new Set(["annotations", "review-pr", "moderation"]);

export function helpCategoryForTutorial(tutorialId: string): HelpCategory {
  if (CREATE_TUTORIALS.has(tutorialId)) return "create";
  if (COLLABORATION_TUTORIALS.has(tutorialId)) return "collaborate";
  return "gettingStarted";
}

function normalizeSearch(value: string): string {
  return value
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLocaleLowerCase()
    .trim();
}

export function matchesHelpQuery(texts: readonly string[], query: string): boolean {
  const needle = normalizeSearch(query);
  if (!needle) return true;
  return texts.some((text) => normalizeSearch(text).includes(needle));
}
