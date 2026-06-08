let inMemoryToken: string | null = null;

const AUTH_HINT_KEY = "lectern_auth_hint";

export function getAccessToken(): string | null {
    return inMemoryToken;
}

export function setAccessToken(token: string): void {
    inMemoryToken = token;
    if (typeof window !== "undefined") {
        localStorage.setItem(AUTH_HINT_KEY, "true");
    }
}

export function clearAccessToken(): void {
    inMemoryToken = null;
    if (typeof window !== "undefined") {
        localStorage.removeItem(AUTH_HINT_KEY);
    }
}

export function hasAuthHint(): boolean {
    if (typeof window === "undefined") return false;
    return localStorage.getItem(AUTH_HINT_KEY) === "true";
}

export function decodeToken(token: string): { exp: number } | null {
    try {
        const payload = token.split(".")[1];
        if (!payload) return null;
        const decoded = JSON.parse(atob(payload));
        return decoded;
    } catch {
        return null;
    }
}
