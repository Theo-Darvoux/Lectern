import React, { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mockApiFetch = vi.fn();

vi.mock("next-intl", () => ({
    useTranslations: () => (key: string) => key,
}));

vi.mock("@/lib/api-client", () => ({
    apiFetch: (...args: unknown[]) => mockApiFetch(...args),
}));

vi.mock("@/components/shader-text", () => ({
    ShaderText: ({ text }: { text: string }) => <div>{text}</div>,
}));

import ResetPasswordPage from "./page";
import { useConfigStore } from "@/lib/stores";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;

async function renderPage() {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => {
        root.render(<ResetPasswordPage />);
    });
}

function setInput(input: HTMLInputElement, value: string) {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
    setter?.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
}

describe("ResetPasswordPage", () => {
    beforeEach(() => {
        vi.clearAllMocks();
        window.history.replaceState(null, "", "/login/reset-password");
        useConfigStore.setState({
            config: {
                classic_enabled: true,
                site_name: "Lectern",
            } as ReturnType<typeof useConfigStore.getState>["config"],
        });
    });

    afterEach(() => {
        act(() => root.unmount());
        container.remove();
    });

    it("requests a reset without revealing whether the account exists", async () => {
        mockApiFetch.mockResolvedValueOnce({
            message: "If an account exists, a password reset link has been sent",
        });
        await renderPage();

        const email = container.querySelector<HTMLInputElement>("#reset-email")!;
        setInput(email, "person@example.com");
        await act(async () => {
            email.form!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
        });

        expect(mockApiFetch).toHaveBeenCalledWith(
            "/auth/password-reset/request",
            expect.objectContaining({
                method: "POST",
                body: JSON.stringify({ email: "person@example.com" }),
                skipAuth: true,
            }),
        );
        expect(container.textContent).toContain("passwordResetEmailSent");
    });

    it("captures and scrubs the fragment token before submitting a new password", async () => {
        window.history.replaceState(null, "", "/login/reset-password#token=secret-token");
        mockApiFetch.mockResolvedValueOnce({ message: "Password reset" });
        await renderPage();

        expect(window.location.hash).toBe("");
        const password = container.querySelector<HTMLInputElement>("#new-password")!;
        const confirmation = container.querySelector<HTMLInputElement>("#confirm-password")!;
        setInput(password, "a-new-secure-password");
        setInput(confirmation, "a-new-secure-password");
        await act(async () => {
            password.form!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
        });

        expect(mockApiFetch).toHaveBeenCalledWith(
            "/auth/password-reset/confirm",
            expect.objectContaining({
                body: JSON.stringify({
                    token: "secret-token",
                    password: "a-new-secure-password",
                }),
                skipAuth: true,
            }),
        );
        expect(container.textContent).toContain("passwordResetSuccess");
    });
});
