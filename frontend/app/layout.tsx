import "./styles.css";
import type { ReactNode } from "react";

export const metadata = {
  title: "Market Brief Agents",
  description: "Daily educational market recaps.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
