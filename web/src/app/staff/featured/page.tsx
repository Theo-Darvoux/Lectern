"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

export default function StaffFeaturedRedirect() {
  const router = useRouter();

  useEffect(() => {
    router.replace("/staff/content?tab=featured");
  }, [router]);

  return null;
}
