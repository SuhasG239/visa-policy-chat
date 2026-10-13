import type { Metadata, Viewport } from "next";
import { Geist, Geist_Mono, Source_Serif_4 } from "next/font/google";
import "./globals.css";

// next/font self-hosts these at build time: the browser makes no requests to Google.
const geist = Geist({ subsets: ["latin"], variable: "--font-geist" });
const geistMono = Geist_Mono({ subsets: ["latin"], variable: "--font-geist-mono" });
const sourceSerif = Source_Serif_4({ subsets: ["latin"], variable: "--font-source-serif" });

export const metadata: Metadata = {
  title: "Visa Policy Chat",
  description: "F-1 student rules, answered from official U.S. government sources with citations.",
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#ffffff" },
    { media: "(prefers-color-scheme: dark)", color: "#15191d" },
  ],
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${geist.variable} ${geistMono.variable} ${sourceSerif.variable}`}>
      <body className="min-h-dvh bg-bg text-fg">
        <a
          href="#question"
          className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50 focus:rounded-lg focus:bg-accent focus:px-4 focus:py-2 focus:text-sm focus:font-semibold focus:text-accent-fg"
        >
          Skip to question box
        </a>
        {children}
      </body>
    </html>
  );
}
