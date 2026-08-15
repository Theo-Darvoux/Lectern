"use client";

import { useTranslations } from "next-intl";
import { useConfigStore } from "@/lib/stores";

export default function PrivacyPage() {
    const t = useTranslations("Privacy");
    const config = useConfigStore((state) => state.config);

    if (!config) return null;

    const configValues = {
        siteName: config.site_name || "",
        legalName: config.legal_name || "[LEGAL NAME]",
        legalAddress: config.legal_address || "[LEGAL ADDRESS]",
        contactEmail: config.contact_email || "[CONTACT EMAIL]",
        dpoEmail: config.dpo_email || "[DPO EMAIL]",
        dpoAddress: config.dpo_address || "[DPO ADDRESS]",
        dataTransfers: config.data_transfers || "[DATA TRANSFERS]",
        date: new Date().toLocaleDateString()
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
                    <h2 className="text-xl font-semibold">{t("sections.controller.title")}</h2>
                    <div className="space-y-4 text-muted-foreground leading-relaxed">
                        <p>{t("sections.controller.text", configValues)}</p>
                        <p>{t("sections.controller.dpo", configValues)}</p>
                    </div>
                </section>

                <section className="space-y-3">
                    <h2 className="text-xl font-semibold">{t("sections.dataCollected.title")}</h2>
                    <div className="space-y-4 text-muted-foreground leading-relaxed">
                        <p>{t("sections.dataCollected.text", configValues)}</p>
                        <ul className="list-disc pl-6 space-y-2">
                            <li>{t("sections.dataCollected.items.account")}</li>
                            <li>{t("sections.dataCollected.items.activity")}</li>
                            <li>{t("sections.dataCollected.items.technical")}</li>
                        </ul>
                    </div>
                </section>

                <section className="space-y-3">
                    <h2 className="text-xl font-semibold">{t("sections.purposes.title")}</h2>
                    <div className="space-y-4 text-muted-foreground leading-relaxed">
                        <p>{t("sections.purposes.text")}</p>
                        <ul className="list-disc pl-6 space-y-2">
                            <li>{t("sections.purposes.items.access")}</li>
                            <li>{t("sections.purposes.items.collaboration")}</li>
                            <li>{t("sections.purposes.items.security")}</li>
                            <li>{t("sections.purposes.items.optimization")}</li>
                        </ul>
                    </div>
                </section>

                <section className="space-y-3">
                    <h2 className="text-xl font-semibold">{t("sections.retention.title")}</h2>
                    <div className="space-y-4 text-muted-foreground leading-relaxed">
                        <ul className="list-disc pl-6 space-y-2">
                            <li>{t("sections.retention.account")}</li>
                            <li>{t("sections.retention.content")}</li>
                            <li>{t("sections.retention.logs")}</li>
                        </ul>
                    </div>
                </section>

                <section className="space-y-3">
                    <h2 className="text-xl font-semibold">{t("sections.recipients.title")}</h2>
                    <div className="space-y-4 text-muted-foreground leading-relaxed">
                        <p>{t("sections.recipients.text", configValues)}</p>
                        <p>{t("sections.recipients.transfers", configValues)}</p>
                    </div>
                </section>

                <section className="space-y-3">
                    <h2 className="text-xl font-semibold">{t("sections.rights.title")}</h2>
                    <div className="space-y-4 text-muted-foreground leading-relaxed">
                        <p>{t("sections.rights.text")}</p>
                        <ul className="list-disc pl-6 space-y-2">
                            <li>{t("sections.rights.items.access")}</li>
                            <li>{t("sections.rights.items.erasure")}</li>
                            <li>{t("sections.rights.items.restriction")}</li>
                            <li>{t("sections.rights.items.opposition")}</li>
                            <li>{t("sections.rights.items.portability")}</li>
                        </ul>
                        <p>{t("sections.rights.exercise", configValues)}</p>
                        <p className="text-sm italic">{t("sections.rights.complaint")}</p>
                    </div>
                </section>

                <section className="space-y-3">
                    <h2 className="text-xl font-semibold">{t("sections.security.title")}</h2>
                    <div className="space-y-4 text-muted-foreground leading-relaxed">
                        <p>{t("sections.security.text")}</p>
                    </div>
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
