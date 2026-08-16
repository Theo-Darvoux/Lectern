"use client";

import { useState } from "react";
import {
    Dialog,
    DialogContent,
    DialogHeader,
    DialogTitle,
    DialogDescription,
    DialogFooter,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { ExternalLink, ShieldAlert, Globe } from "lucide-react";
import { useExternalLinkStore } from "@/lib/external-link-store";
import { useTranslations } from "next-intl";

export function ExternalLinkDialog() {
    const t = useTranslations("ExternalLinkDialog");
    const isOpen = useExternalLinkStore((s) => s.isOpen);
    const targetUrl = useExternalLinkStore((s) => s.targetUrl);
    const domain = useExternalLinkStore((s) => s.domain);
    const closeDialog = useExternalLinkStore((s) => s.closeDialog);
    const confirmAndOpen = useExternalLinkStore((s) => s.confirmAndOpen);

    const [trustDomain, setTrustDomain] = useState(false);

    const handleOpenChange = (open: boolean) => {
        if (!open) {
            setTrustDomain(false);
            closeDialog();
        }
    };

    const handleConfirm = () => {
        confirmAndOpen(trustDomain);
        setTrustDomain(false);
    };

    return (
        <Dialog open={isOpen} onOpenChange={handleOpenChange}>
            <DialogContent className="sm:max-w-md">
                <DialogHeader className="gap-2">
                    <div className="flex items-center gap-3">
                        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-amber-100 text-amber-600 dark:bg-amber-950/50 dark:text-amber-400">
                            <ShieldAlert className="h-5 w-5" />
                        </div>
                        <div>
                            <DialogTitle className="text-base font-semibold leading-none">
                                {t("title")}
                            </DialogTitle>
                            <DialogDescription className="mt-1 text-xs text-muted-foreground">
                                {t("description")}
                            </DialogDescription>
                        </div>
                    </div>
                </DialogHeader>

                <div className="space-y-4 py-2">
                    {/* Destination URL Display */}
                    <div className="relative flex items-start gap-2.5 rounded-lg border border-border/80 bg-muted/60 dark:bg-zinc-900/80 p-3 text-xs">
                        <Globe className="h-4 w-4 shrink-0 text-muted-foreground mt-0.5" />
                        <div className="min-w-0 flex-1">
                            <span className="block font-mono text-[13px] text-foreground break-all select-all font-medium">
                                {targetUrl}
                            </span>
                        </div>
                    </div>

                    {/* Disclaimer text */}
                    <p className="text-xs text-muted-foreground leading-relaxed">
                        {t("disclaimer")}
                    </p>

                    {/* Trust domain checkbox */}
                    {domain && (
                        <div className="flex items-center space-x-2 pt-1">
                            <Checkbox
                                id="trust-domain-checkbox"
                                checked={trustDomain}
                                onCheckedChange={(checked) => setTrustDomain(checked === true)}
                            />
                            <Label
                                htmlFor="trust-domain-checkbox"
                                className="text-xs text-muted-foreground font-normal cursor-pointer select-none"
                            >
                                {t("trustDomain", { domain })}
                            </Label>
                        </div>
                    )}
                </div>

                <DialogFooter className="gap-2.5 sm:gap-2.5">
                    <Button variant="outline" size="sm" onClick={() => handleOpenChange(false)}>
                        {t("cancel")}
                    </Button>
                    <Button size="sm" onClick={handleConfirm} className="gap-1.5 font-medium">
                        <ExternalLink className="h-3.5 w-3.5" />
                        {t("visitSite")}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
