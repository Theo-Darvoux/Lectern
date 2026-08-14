import Link from "next/link";
import { useConfigStore } from "@/lib/stores";
import { useTranslations } from "next-intl";

// Baked in at build time from the Docker image's commit SHA (see web/Dockerfile
// and the `NEXT_PUBLIC_COMMIT_SHA` build-arg in .github/workflows/build.yml).
const commitSha = process.env.NEXT_PUBLIC_COMMIT_SHA;

export function Footer() {
    const t = useTranslations("Layout");
    const config = useConfigStore((state) => state.config);
    const shortCommit = commitSha?.slice(0, 7);
    const repoUrl = config?.repo_url || process.env.NEXT_PUBLIC_REPO_URL || "";

    return (
        <footer className={`border-t pt-6 ${config?.footer_text ? "pb-10" : "pb-6"} w-full`}>
            <div className="relative flex items-center justify-center px-6">
                {config?.footer_logo_url && (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                        src={config.footer_logo_url}
                        alt="Footer logo"
                        className="absolute left-6 h-8 w-auto object-contain opacity-80"
                    />
                )}
                <div className="flex items-center gap-4 text-sm text-muted-foreground">
                    <Link href="/privacy" className="hover:text-foreground transition-colors">
                        {t("privacyPolicy")}
                    </Link>
                    <span aria-hidden>•</span>
                    <div className="relative">
                        <Link href="/terms" className="hover:text-foreground transition-colors">
                            {t("termsOfUse")}
                        </Link>
                        {config?.footer_text && (
                            <p className="absolute top-full pt-1.5 left-1/2 -translate-x-1/2 text-xs whitespace-nowrap text-muted-foreground">
                                {config.footer_text}
                            </p>
                        )}
                    </div>
                    {(config?.organization_url || repoUrl) && (
                        <>
                            <span aria-hidden>•</span>
                            <a
                                href={config?.organization_url || repoUrl}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="hover:text-foreground transition-colors"
                            >
                                {config?.organization_url ? t("organization") : t("github")}
                            </a>
                        </>
                    )}
                </div>
                {shortCommit && repoUrl && (
                    <a
                        href={`${repoUrl}/commit/${commitSha}`}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="absolute right-6 font-mono text-xs text-muted-foreground/60 hover:text-foreground transition-colors"
                        title={commitSha}
                    >
                        {t("commit")}: #{shortCommit}
                    </a>
                )}
            </div>
        </footer>
    );
}
