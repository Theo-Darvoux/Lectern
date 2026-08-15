"use client";

import { useTranslations } from "next-intl";
import { useConfigStore } from "@/lib/stores";

export default function TermsPage() {
    const t = useTranslations("Terms");
    const config = useConfigStore((state) => state.config);

    if (!config) return null;

    const configValues = {
        siteName: config.site_name || "",
        legalName: config.legal_name || "[LEGAL NAME]",
        legalAddress: config.legal_address || "[LEGAL ADDRESS]",
        legalSiret: config.legal_siret || "[SIRET]",
        date: new Date().toLocaleDateString(),
    };

    return (
        <div className="w-full mx-auto max-w-3xl space-y-8 py-12 px-4 animate-in fade-in slide-in-from-bottom-4 duration-500">
            <header className="space-y-2 border-b pb-6">
                <h1 className="text-3xl font-bold tracking-tight">{t("title", configValues)}</h1>
                <p className="text-sm text-muted-foreground">
                    {t("version", { 
                        version: config.legal_version || "1.0",
                        date: configValues.date 
                    })}
                </p>
            </header>

            <div className="space-y-10">
                <section className="space-y-3">
                    <h2 className="text-xl font-semibold">{t("sections.presentation.title")}</h2>
                    <p className="text-muted-foreground leading-relaxed">
                        {t("sections.presentation.text", configValues)}
                    </p>
                </section>

                <section className="space-y-3">
                    <h2 className="text-xl font-semibold">{t("sections.access.title")}</h2>
                    <p className="text-muted-foreground leading-relaxed">
                        {t("sections.access.text", configValues)}
                    </p>
                </section>

                <section className="space-y-3">
                    <h2 className="text-xl font-semibold">{t("sections.content.title")}</h2>
                    <div className="space-y-4 text-muted-foreground leading-relaxed">
                        <p>{t("sections.content.text1", configValues)}</p>
                        <p>{t("sections.content.text2", configValues)}</p>
                        <ul className="list-disc pl-6 space-y-2">
                            <li>{t("sections.content.scope")}</li>
                            <li>{t("sections.content.purpose", configValues)}</li>
                            <li>{t("sections.content.location", configValues)}</li>
                        </ul>
                        <p>{t("sections.content.text3")}</p>
                        <p>{t("sections.content.text4")}</p>
                    </div>
                </section>

                <section className="space-y-3">
                    <h2 className="text-xl font-semibold">{t("sections.prohibited.title")}</h2>
                    <p className="text-muted-foreground leading-relaxed">
                        {t("sections.prohibited.text", configValues)}
                    </p>
                </section>

                <section className="space-y-3">
                    <h2 className="text-xl font-semibold">{t("sections.availability.title")}</h2>
                    <p className="text-muted-foreground leading-relaxed">
                        {t("sections.availability.text", configValues)}
                    </p>
                </section>

                <section className="space-y-3">
                    <h2 className="text-xl font-semibold">{t("sections.modification.title")}</h2>
                    <p className="text-muted-foreground leading-relaxed">
                        {t("sections.modification.text")}
                    </p>
                </section>

                <section className="space-y-3">
                    <h2 className="text-xl font-semibold">{t("sections.law.title")}</h2>
                    <p className="text-muted-foreground leading-relaxed">
                        {t("sections.law.text", configValues)}
                    </p>
                </section>
            </div>

            <footer className="pt-8 mt-12 border-t border-border/50 pb-8 text-sm text-muted-foreground text-center">
                <p>
                    © {new Date().getFullYear()} {config.site_name} • {t("title", configValues)}
                </p>
                <p className="mt-1">{configValues.legalAddress}</p>
            </footer>
        </div>
    );
}
