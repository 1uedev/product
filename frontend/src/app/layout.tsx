import type { Metadata } from "next";
import type { ReactNode } from "react";
import "./globals.css";
import { Providers } from "@/components/providers";

export const metadata: Metadata = {
  title: "decision-evidence",
  description: "Kundenfeedback zu begründeten Produktentscheidungen",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="de">
      <body><Providers>{children}</Providers></body>
    </html>
  );
}
