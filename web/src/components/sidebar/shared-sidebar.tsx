"use client";

import { useEffect, useState } from "react";
import { useUIStore, type SidebarTab } from "@/lib/stores";
import { cn, isRestrictedTarget } from "@/lib/utils";
import { useIsDesktop } from "@/hooks/use-media-query";
import { useSearchParams } from "next/navigation";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import dynamic from "next/dynamic";
import { Skeleton } from "@/components/ui/skeleton";
import { useTranslations } from "next-intl";

// --- Dynamic Tab Imports ---
// This ensures that heavy tab components (like Chat and Annotations) are only 
// compiled when they are actually opened, reducing memory pressure in dev.

import { DetailsTab } from "@/components/sidebar/details-tab";

const ChatTab = dynamic(() => import("@/components/sidebar/chat-tab").then(mod => mod.ChatTab), {
  loading: () => <div className="flex-1 flex items-center justify-center p-8"><Skeleton className="h-full w-full" /></div>,
  ssr: false
});


const EditsTab = dynamic(() => import("@/components/sidebar/edits-tab").then(mod => mod.EditsTab), {
  loading: () => <div className="p-4 space-y-4"><Skeleton className="h-40 w-full" /></div>,
  ssr: false
});

const AnnotationsTab = dynamic(() => import("@/components/sidebar/annotations-tab").then(mod => mod.AnnotationsTab), {
  loading: () => <div className="flex-1 flex items-center justify-center p-8"><Skeleton className="h-full w-full" /></div>,
  ssr: false
});
import { X, Info } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Drawer,
  DrawerContent,
  DrawerTitle,
} from "@/components/ui/drawer";

const TAB_CONFIG: { value: SidebarTab; labelKey: string }[] = [
  { value: "details", labelKey: "details" },
  { value: "chat", labelKey: "chat" },
  { value: "annotations", labelKey: "annotations" },
  { value: "edits", labelKey: "edits" },
];

function SidebarContent() {
  const { sidebarTab, setSidebarTab, sidebarTarget, closeSidebar } =
    useUIStore();
  const isDesktop = useIsDesktop();
  const t = useTranslations("Sidebar");
  const searchParams = useSearchParams();

  const isRestricted = isRestrictedTarget(sidebarTarget?.id, searchParams.get("preview_pr"));

  // Defensive auto-fallback: if the active tab is one disabled for restricted
  // targets, switch to "details" so the user never sees a locked panel.
  useEffect(() => {
    const restrictedTabs: SidebarTab[] = ["chat", "annotations", "edits"];
    if (isRestricted && restrictedTabs.includes(sidebarTab)) {
      setSidebarTab("details");
    }
  }, [isRestricted, sidebarTab, setSidebarTab]);

  return (
    <div className="flex flex-1 flex-col min-h-0 bg-background overflow-hidden">
      {/* Header - Only visible on desktop as Drawer handles its own header/dismissal needs */}
      {isDesktop && (
        <div className="flex items-center justify-between border-b px-3 py-2 shrink-0 bg-muted/10">
          <span className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground/70 flex items-center gap-2">
            <Info className="h-3 w-3" />
            {t("itemInspector")}
          </span>
          <Button
            variant="ghost"
            size="sm"
            className="h-7 w-7 p-0 rounded-full hover:bg-destructive/10 hover:text-destructive"
            onClick={closeSidebar}
            title={t("closeSidebar")}
          >
            <X className="h-4 w-4" />
          </Button>
        </div>
      )}

      {/* Tabs */}
      <Tabs
        value={sidebarTab}
        onValueChange={(v) => setSidebarTab(v as SidebarTab)}
        className="flex flex-1 min-h-0 flex-col gap-0 overflow-hidden"
      >
        <TabsList
          variant="line"
          className="w-full shrink-0 border-b bg-transparent px-1"
        >
          {TAB_CONFIG.map((tab) => {
            const isTabRestricted = isRestricted && (tab.value === "chat" || tab.value === "annotations" || tab.value === "edits");
            return (
              <TabsTrigger
                key={tab.value}
                value={tab.value}
                className={cn("px-2.5 py-2 text-xs transition-opacity", isTabRestricted && "opacity-40 cursor-not-allowed")}
                disabled={isTabRestricted}
              >
                {t(tab.labelKey as Parameters<typeof t>[0])}
              </TabsTrigger>
            );
          })}
        </TabsList>

        <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
          <TabsContent
            value="details"
            className="flex-1 flex flex-col min-h-0 m-0 data-[state=inactive]:hidden"
          >
            <div className="flex-1 overflow-y-auto p-4 custom-scrollbar">
              <DetailsTab target={sidebarTarget} />
            </div>
          </TabsContent>

          <TabsContent
            value="chat"
            className="flex-1 flex flex-col min-h-0 m-0 data-[state=inactive]:hidden"
          >
            <ChatTab target={sidebarTarget} disabled={isRestricted} />
          </TabsContent>

          <TabsContent
            value="annotations"
            className="flex-1 flex flex-col min-h-0 m-0 data-[state=inactive]:hidden"
          >
            <div className="flex-1 overflow-y-auto p-4 custom-scrollbar">
              <AnnotationsTab target={sidebarTarget} disabled={isRestricted} />
            </div>
          </TabsContent>

          <TabsContent
            value="edits"
            className="flex-1 flex flex-col min-h-0 m-0 data-[state=inactive]:hidden"
          >
            <div className="flex-1 overflow-y-auto p-4 custom-scrollbar">
              <EditsTab target={sidebarTarget} />
            </div>
          </TabsContent>
        </div>
      </Tabs>
    </div>
  );
}

export function SharedSidebar() {
  const { sidebarOpen, closeSidebar } = useUIStore();
  const isDesktop = useIsDesktop();
  const t = useTranslations("Sidebar");
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  if (!mounted) return null;

  // Desktop: render directly inside the page's bounded container with transition
  if (isDesktop) {
    return (
      <aside
        className={cn(
          "relative h-full min-h-full transition-all duration-150 ease-in-out overflow-hidden bg-background border-l border-border",
          sidebarOpen ? "w-80 opacity-100" : "w-0 opacity-0 border-transparent pointer-events-none"
        )}
      >
        <div className="w-80 h-full min-h-full flex flex-col overflow-hidden">
          <SidebarContent />
        </div>
      </aside>
    );
  }

  // Mobile: Drawer for native feel and swipe-to-dismiss
  return (
    <Drawer open={sidebarOpen} onOpenChange={(o) => !o && closeSidebar()}>
      <DrawerContent className="h-[90dvh] pb-0 outline-none overflow-hidden">
        <DrawerTitle className="sr-only">{t("itemInspector")}</DrawerTitle>
        <div className="flex-1 overflow-hidden">
          <SidebarContent />
        </div>
      </DrawerContent>
    </Drawer>
  );
}
