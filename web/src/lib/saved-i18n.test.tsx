import { describe, it, expect, beforeEach, afterEach } from "vitest";
import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { NextIntlClientProvider } from "next-intl";
import { useSavedTranslations } from "./saved-i18n";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe("useSavedTranslations", () => {
  let container: HTMLDivElement;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
  });

  afterEach(() => {
    if (container) {
      container.remove();
    }
  });

  it("provides translations in English", async () => {
    let t: ReturnType<typeof useSavedTranslations> | undefined;

    function TestComponent() {
      const translations = useSavedTranslations();
      React.useEffect(() => {
        t = translations;
      }, [translations]);
      return null;
    }

    const root = createRoot(container);
    await act(async () => {
      root.render(
        <NextIntlClientProvider locale="en" messages={{}}>
          <TestComponent />
        </NextIntlClientProvider>,
      );
    });

    expect(t).toBeDefined();
    if (!t) return;
    expect(t("title")).toBe("Saved Library");
    expect(t("allSaved")).toBe("All saved");
    expect(t("itemCount", { count: 1 })).toBe("1 item");
    expect(t("itemCount", { count: 5 })).toBe("5 items");
    expect(t("selectedCount", { count: 3 })).toBe("3 selected");
    expect(t("collectionCreated", { name: "Exam Prep" })).toBe('Collection “Exam Prep” created');
  });

  it("provides translations in French", async () => {
    let t: ReturnType<typeof useSavedTranslations> | undefined;

    function TestComponent() {
      const translations = useSavedTranslations();
      React.useEffect(() => {
        t = translations;
      }, [translations]);
      return null;
    }

    const root = createRoot(container);
    await act(async () => {
      root.render(
        <NextIntlClientProvider locale="fr" messages={{}}>
          <TestComponent />
        </NextIntlClientProvider>,
      );
    });

    expect(t).toBeDefined();
    if (!t) return;
    expect(t("title")).toBe("Bibliothèque d'enregistrés");
    expect(t("allSaved")).toBe("Tous les enregistrés");
    expect(t("itemCount", { count: 1 })).toBe("1 élément");
    expect(t("itemCount", { count: 5 })).toBe("5 éléments");
    expect(t("selectedCount", { count: 3 })).toBe("3 sélectionnés");
  });
});
