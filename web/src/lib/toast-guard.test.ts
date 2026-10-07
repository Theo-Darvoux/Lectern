import { describe, it, expect, vi, beforeAll, beforeEach } from "vitest";

const { showError, dismiss } = vi.hoisted(() => ({
  showError: vi.fn((..._args: unknown[]) => "toast-id"),
  dismiss: vi.fn((id?: string | number) => id ?? ""),
}));

vi.mock("sonner", () => ({
  toast: { error: showError, dismiss },
}));

import { toast } from "sonner";
import { installToastGuard } from "./toast-guard";

function setReachable(reachable: boolean) {
  window.dispatchEvent(
    new CustomEvent(reachable ? "lectern-api-reachable" : "lectern-api-unreachable"),
  );
}

describe("toast-guard", () => {
  beforeAll(() => {
    installToastGuard();
  });

  beforeEach(() => {
    vi.clearAllMocks();
    setReachable(true);
  });

  it("passes regular error toasts through", () => {
    toast.error("Something failed");
    expect(showError).toHaveBeenCalledWith("Something failed", undefined);
  });

  it("drops error toasts with a blank message", () => {
    toast.error("");
    toast.error("   ");
    expect(showError).not.toHaveBeenCalled();
  });

  it("drops error toasts while the API is unreachable, and resumes after", () => {
    setReachable(false);
    toast.error("HTTP 502");
    toast.error("Failed to fetch");
    expect(showError).not.toHaveBeenCalled();

    setReachable(true);
    toast.error("Real error");
    expect(showError).toHaveBeenCalledTimes(1);
  });

  it("dismisses the targeted toast instead of leaving a loading toast behind", () => {
    setReachable(false);
    toast.error("HTTP 502", { id: "upload-1" });
    expect(dismiss).toHaveBeenCalledWith("upload-1");
    expect(showError).not.toHaveBeenCalled();
  });
});
