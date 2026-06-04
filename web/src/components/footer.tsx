import Link from "next/link";
import { useConfigStore } from "@/lib/stores";
import { useTranslations } from "next-intl";

export function Footer() {
    const t = useTranslations("Layout");
    const { config } = useConfigStore();

    return (
        <footer className="border-t py-6 w-full">
            <div className="relative flex items-center justify-center px-6">
                {config?.footer_logo_url && (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                        src={config.footer_logo_url}
                        alt="Footer logo"
                        className="absolute left-6 h-8 w-auto object-contain opacity-80"
                    />
                )}
                <div className="flex flex-col items-center gap-1.5 text-sm text-muted-foreground">
                    <div className="flex items-center gap-4">
                        <Link href="/privacy" className="hover:text-foreground transition-colors">
                            {t("privacyPolicy")}
                        </Link>
                        <span aria-hidden>•</span>
                        <div className="flex flex-col items-center gap-1.5">
                            <Link href="/terms" className="hover:text-foreground transition-colors">
                                {t("termsOfUse")}
                            </Link>
                            {config?.footer_text && (
                                <p className="text-xs whitespace-nowrap">{config.footer_text}</p>
                            )}
                        </div>
                        <span aria-hidden>•</span>
                        <a
                            href={config?.organization_url || "https://github.com/Theo-Darvoux/WikINT"}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="hover:text-foreground transition-colors"
                        >
                            {config?.organization_url ? t("organization") : t("github")}
                        </a>
                    </div>
                </div>
            </div>
        </footer>
    );
}
