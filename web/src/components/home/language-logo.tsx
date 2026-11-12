import type { ElementType } from "react";
import type { SimpleIcon } from "simple-icons";

/** A Simple Icons logo, drawn in `currentColor` on its 24×24 grid. */
export function LanguageLogo({ icon, className }: { icon: SimpleIcon; className?: string }) {
    return (
        <svg data-language-logo={icon.slug} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true" focusable="false" className={className}>
            <path d={icon.path} />
        </svg>
    );
}

const LOGO_COMPONENTS = new Map<SimpleIcon, ElementType<{ className?: string }>>();

/**
 * The logo as a stable icon component, so it can stand in for a Lucide icon
 * (`<Icon className=… />`). Cached per logo: components must not be recreated
 * on every render.
 */
export function languageLogoIcon(icon: SimpleIcon): ElementType<{ className?: string }> {
    let component = LOGO_COMPONENTS.get(icon);
    if (!component) {
        const Logo = ({ className }: { className?: string }) => <LanguageLogo icon={icon} className={className} />;
        Logo.displayName = `LanguageLogo(${icon.title})`;
        component = Logo;
        LOGO_COMPONENTS.set(icon, component);
    }
    return component;
}
