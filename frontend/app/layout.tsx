import type { Metadata } from "next";
import { Oswald, Noto_Serif_Georgian, Roboto_Mono} from "next/font/google";
import Navbar from "@/components/layout/navbar";
import Footer from "@/components/layout/footer";
import { Toaster } from "@/components/ui/sonner";
import { Providers } from "./providers";
import "./globals.css";


const oswald = Oswald({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-oswald",
});

const notoSerifGeorgian = Noto_Serif_Georgian({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-noto-serif-georgian",
});

const robotoMono = Roboto_Mono({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-roboto-mono",
  weight: ['300', '400', '500', '700']
});

export const metadata: Metadata = {
  title: "ChainIQ – Procurement Audit",
  description: "AI-powered procurement request auditing and supplier evaluation platform.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body
        className={`${oswald.variable} ${notoSerifGeorgian.variable} ${robotoMono.variable} antialiased`}
      >
        <Providers>
          <main>
            <Navbar/>
              {children}
            <Footer/>
          </main>
          <Toaster />
        </Providers>
      </body>
    </html>
  );
}
