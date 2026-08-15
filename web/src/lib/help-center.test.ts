import { describe, expect, it } from "vitest";

import { helpCategoryForTutorial, matchesHelpQuery } from "./help-center";

describe("help center organization", () => {
  it("groups tutorials by the task they help complete", () => {
    expect(helpCategoryForTutorial("welcome")).toBe("gettingStarted");
    expect(helpCategoryForTutorial("upload")).toBe("create");
    expect(helpCategoryForTutorial("annotations")).toBe("collaborate");
  });

  it("matches localized help text case- and accent-insensitively", () => {
    expect(matchesHelpQuery(["Téléverser un document", "Fichiers"], "televerser")).toBe(true);
    expect(matchesHelpQuery(["Open a document"], "upload")).toBe(false);
  });
});
