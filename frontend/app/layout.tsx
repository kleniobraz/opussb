import type { Metadata, Viewport } from "next";
import { Space_Grotesk, Archivo, Inter, JetBrains_Mono } from "next/font/google";
import "./globals.css";
import { AppProviders } from "@/providers/app-providers";

const spaceGrotesk = Space_Grotesk({
  variable: "--font-sans",
  subsets: ["latin"],
  weight: ["300", "400", "500", "600", "700"],
  display: "swap",
});

// Fonte opt-in do redesign v3 (fundacao apenas; Space Grotesk continua a fonte padrao).
const archivo = Archivo({
  variable: "--v3-font-display",
  subsets: ["latin"],
  axes: ["wdth"], // necessario pro font-stretch 112-115% do spec funcionar
  display: "swap",
});

// Teste: Inter pro corpo de texto da v3 (Archivo fica so pros titulos em CAPS).
const inter = Inter({
  variable: "--v3-font-sans",
  subsets: ["latin"],
  display: "swap",
});

const jetbrainsMono = JetBrains_Mono({
  variable: "--font-mono",
  subsets: ["latin"],
  display: "swap",
});

export const metadata: Metadata = {
  title: "SB Clips · Corte Viral",
  description:
    "A central de clipes do Grupo SB. Envie um vídeo longo e receba os melhores momentos em 9:16.",
  icons: { icon: "/favicon.svg", apple: "/favicon.svg" },
};

export const viewport: Viewport = {
  themeColor: "#0a0a0b",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="pt-BR"
      className={`${spaceGrotesk.variable} ${archivo.variable} ${inter.variable} ${jetbrainsMono.variable} dark h-full antialiased`}
    >
      <body className="min-h-full">
        <AppProviders>{children}</AppProviders>
      </body>
    </html>
  );
}
