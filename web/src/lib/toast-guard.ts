import { toast } from "sonner";

let installed = false;
let apiUnreachable = false;

/**
 * Filter error toasts that carry no information for the user:
 *  - empty messages (e.g. an error whose message was a blank statusText);
 *  - any error while the API is unreachable. The layout's offline banner
 *    already explains the outage; without this, every background request that
 *    fails during an API restart stacks its own toast.
 *
 * A dropped toast that targets an existing id (loading → error) dismisses that
 * toast instead, so no spinner is left behind.
 */
export function installToastGuard() {
    if (installed || typeof window === "undefined") return;
    installed = true;

    window.addEventListener("lectern-api-unreachable", () => { apiUnreachable = true; });
    window.addEventListener("lectern-api-reachable", () => { apiUnreachable = false; });

    const showError = toast.error;
    toast.error = (message, data) => {
        const isBlank = message == null || message === false
            || (typeof message === "string" && !message.trim());
        if (isBlank || apiUnreachable) {
            if (data?.id !== undefined) return toast.dismiss(data.id);
            return "";
        }
        return showError(message, data);
    };
}
