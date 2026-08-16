"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

export default function StaffDirectoriesRedirect() {
  const router = useRouter();

  useEffect(() => {
    router.replace("/staff/content?tab=directories");
  }, [router]);

  return null;
}
