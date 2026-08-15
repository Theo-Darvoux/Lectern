"use client";

import Link from "next/link";
import {
  forwardRef,
  type ComponentPropsWithoutRef,
  type MouseEventHandler,
} from "react";

function isBrowseHref(href: string): boolean {
  return (
    href === "/browse" ||
    href.startsWith("/browse/") ||
    href.startsWith("/browse?")
  );
}

/**
 * Update an in-browse URL without asking the App Router for a new RSC payload.
 *
 * `/browse/[[...path]]` is a static-export shell: the current pathname is the
 * client-side browse key, while BrowsePageContent fetches the actual directory
 * or material from the API. Next.js integrates native history updates with
 * usePathname/useSearchParams, so this keeps the existing shell mounted and
 * lets the client data owner react to the new URL directly.
 */
export function navigateBrowse(
  href: string,
  options: { replace?: boolean } = {},
): void {
  if (typeof window === "undefined") return;

  const method = options.replace ? "replaceState" : "pushState";
  window.history[method](null, "", href);
}

type BrowseLinkProps = Omit<
  ComponentPropsWithoutRef<typeof Link>,
  "href" | "onClick" | "prefetch"
> & {
  href: string;
  onClick?: MouseEventHandler<HTMLAnchorElement>;
};

/**
 * Normal Next.js Link semantics outside /browse. Inside /browse, preserve the
 * real anchor href (new-tab/copy-link still work) but use the native History API
 * for an ordinary left click so Next never requests an impossible deep-route
 * RSC payload from the static shell.
 */
export const BrowseLink = forwardRef<HTMLAnchorElement, BrowseLinkProps>(
  function BrowseLink({ href, onClick, target, ...props }, ref) {
    const browseHref = isBrowseHref(href);

    const handleClick: MouseEventHandler<HTMLAnchorElement> = (event) => {
      onClick?.(event);
      if (
        !browseHref ||
        event.defaultPrevented ||
        event.button !== 0 ||
        event.metaKey ||
        event.ctrlKey ||
        event.shiftKey ||
        event.altKey ||
        (target && target !== "_self") ||
        event.currentTarget.hasAttribute("download")
      ) {
        return;
      }

      event.preventDefault();
      navigateBrowse(href);
    };

    return (
      <Link
        {...props}
        ref={ref}
        href={href}
        target={target}
        prefetch={browseHref ? false : undefined}
        onClick={handleClick}
      />
    );
  },
);
