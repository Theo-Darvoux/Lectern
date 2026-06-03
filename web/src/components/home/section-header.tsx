import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { cn } from "@/lib/utils";
import type { ReactNode } from "react";

interface SectionHeaderProps {
  title: string;
  subtitle?: string;
  icon?: ReactNode;
  seeAllHref?: string;
  seeAllLabel?: string;
  className?: string;
}

import { useTranslations } from "next-intl";

export function SectionHeader({
  title,
  subtitle,
  icon,
  seeAllHref,
  seeAllLabel,
  className,
}: SectionHeaderProps) {
  const t = useTranslations("Home");
  const defaultSeeAllLabel = seeAllLabel || t("seeAll");

  return (
    <div className={cn("space-y-0.5", className)}>
      <div className="flex items-center justify-between gap-4">
        <h2 className="flex min-w-0 items-center gap-2 text-lg font-semibold leading-tight tracking-tight sm:text-xl">
          {icon && (
            <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
              {icon}
            </span>
          )}
          <span className="truncate">{title}</span>
        </h2>

        {seeAllHref && (
          <Link
            href={seeAllHref}
            className="inline-flex shrink-0 items-center gap-1 rounded-md px-2 py-1 text-sm font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground"
          >
            {defaultSeeAllLabel}
            <ArrowRight className="h-3.5 w-3.5" />
          </Link>
        )}
      </div>

      {subtitle && <p className="text-sm text-muted-foreground">{subtitle}</p>}
    </div>
  );
}
