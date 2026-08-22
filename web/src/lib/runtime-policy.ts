import { normalizePathname } from "./utils";

const SHELL_FREE_ROUTES = new Set([
    "/setup",
    "/login",
    "/login/verify",
    "/login/reset-password",
]);

/** Routes whose first interaction does not need navigation or contribution UI. */
export function requiresAppRuntime(pathname: string): boolean {
    return !SHELL_FREE_ROUTES.has(normalizePathname(pathname));
}
