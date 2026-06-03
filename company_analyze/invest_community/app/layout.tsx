import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "投资社区",
  description: "股票投资社区平台",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
