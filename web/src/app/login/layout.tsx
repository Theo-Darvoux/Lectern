import type { Metadata } from "next";

const siteName = process.env.SITE_NAME || "Lectern";

export const metadata: Metadata = {
  title: `Sign in • ${siteName}`,
};

export default function LoginLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return children;
}
