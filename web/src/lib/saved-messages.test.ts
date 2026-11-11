import { describe, it, expect } from "vitest";
import { createTranslator } from "next-intl";
import en from "../../messages/en.json";
import fr from "../../messages/fr.json";

describe("Saved messages", () => {
  it("pluralizes item and selection counts in English", () => {
    const t = createTranslator({ locale: "en", messages: en, namespace: "Saved" });
    expect(t("itemCount", { count: 1 })).toBe("1 item");
    expect(t("itemCount", { count: 3 })).toBe("3 items");
    expect(t("selectedCount", { count: 2 })).toBe("2 selected");
  });

  it("pluralizes item and selection counts in French", () => {
    const t = createTranslator({ locale: "fr", messages: fr, namespace: "Saved" });
    expect(t("itemCount", { count: 1 })).toBe("1 élément");
    expect(t("itemCount", { count: 3 })).toBe("3 éléments");
    expect(t("selectedCount", { count: 1 })).toBe("1 sélectionné");
    expect(t("selectedCount", { count: 2 })).toBe("2 sélectionnés");
  });

  it("interpolates names", () => {
    const t = createTranslator({ locale: "en", messages: en, namespace: "Saved" });
    expect(t("collectionCreated", { name: "Maths" })).toContain("Maths");
  });
});
