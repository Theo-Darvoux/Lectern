import type { Metadata } from "next";

// Rendered as "Sign in • <site name>" by the root layout's title template.
export const metadata: Metadata = {
  title: "Sign in",
};

export default function LoginLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return children;
}
