const API_SCRIPT_SELECTOR = "script[data-eurooffice-api]";
const DEFAULT_TIMEOUT_MS = 15_000;

type DocsApiWindow = Window & {
  DocsAPI?: {
    DocEditor: new (containerId: string, config: unknown) => unknown;
  };
};

let pendingLoad: { src: string; promise: Promise<void> } | null = null;

function apiSource(baseUrl: string): string {
  const normalized = baseUrl.endsWith("/") ? baseUrl : `${baseUrl}/`;
  return new URL(
    `${normalized}web-apps/apps/api/documents/api.js`,
    window.location.href,
  ).href;
}

function removeScript(script: HTMLScriptElement): void {
  script.remove();
  if (pendingLoad?.src === script.src) pendingLoad = null;
}

/**
 * Load EuroOffice's browser API once for the whole page.
 *
 * Failed and half-loaded scripts are evicted so a later attempt can genuinely
 * retry instead of subscribing to a dead DOM node. Concurrent viewers share
 * one promise and one script request.
 */
export function loadEuroofficeApi(
  baseUrl: string,
  timeoutMs = DEFAULT_TIMEOUT_MS,
): Promise<void> {
  const docsWindow = window as DocsApiWindow;
  if (docsWindow.DocsAPI?.DocEditor) return Promise.resolve();

  const src = apiSource(baseUrl);
  if (pendingLoad?.src === src) return pendingLoad.promise;

  const staleScripts = document.querySelectorAll<HTMLScriptElement>(API_SCRIPT_SELECTOR);
  for (const stale of staleScripts) {
    if (
      stale.src !== src ||
      stale.dataset.loadState === "failed" ||
      stale.dataset.loadState === "ready"
    ) {
      stale.remove();
    }
  }

  let script = Array.from(staleScripts).find(
    (candidate) => candidate.isConnected && candidate.src === src,
  );
  if (!script) {
    script = document.createElement("script");
    script.src = src;
    script.async = true;
    script.dataset.euroofficeApi = "true";
    script.dataset.loadState = "loading";
    document.head.appendChild(script);
  }

  const promise = new Promise<void>((resolve, reject) => {
    let settled = false;
    const finish = (error?: Error) => {
      if (settled) return;
      settled = true;
      window.clearTimeout(timeoutId);
      script?.removeEventListener("load", onLoad);
      script?.removeEventListener("error", onError);

      if (error) {
        if (script) {
          script.dataset.loadState = "failed";
          removeScript(script);
        }
        reject(error);
      } else {
        if (script) script.dataset.loadState = "ready";
        resolve();
      }
    };

    const onLoad = () => {
      if (docsWindow.DocsAPI?.DocEditor) {
        finish();
      } else {
        finish(new Error("EuroOffice API loaded without exposing DocsAPI"));
      }
    };
    const onError = () => finish(new Error("EuroOffice API script request failed"));
    const timeoutId = window.setTimeout(
      () => finish(new Error("EuroOffice API script request timed out")),
      timeoutMs,
    );

    script?.addEventListener("load", onLoad, { once: true });
    script?.addEventListener("error", onError, { once: true });
  }).finally(() => {
    if (pendingLoad?.promise === promise) pendingLoad = null;
  });

  pendingLoad = { src, promise };
  return promise;
}
