import { PRList } from "@/components/pr/pr-list";
import { Suspense } from "react";
import { Skeleton } from "@/components/ui/skeleton";

function PRListSkeleton() {
  return (
    <div className="space-y-6">
      <Skeleton className="h-44 w-full rounded-3xl" />
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <Skeleton className="h-10 w-full sm:w-80 rounded-xl" />
        <Skeleton className="h-10 w-full sm:w-48 rounded-xl" />
      </div>
      <div className="space-y-3">
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton key={i} className="h-28 w-full rounded-2xl" />
        ))}
      </div>
    </div>
  );
}

export default function PullRequestsPage() {
  return (
    <div className="w-full px-4 py-6 pb-12 sm:px-6 sm:py-8 lg:px-8">
      <div className="mx-auto w-full max-w-5xl">
        <Suspense fallback={<PRListSkeleton />}>
          <PRList />
        </Suspense>
        <div className="h-28 sm:hidden shrink-0 pointer-events-none" aria-hidden="true" />
      </div>
    </div>
  );
}
