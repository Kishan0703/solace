import "./globals.css";
import Link from "next/link";

export const metadata = {
  title: "Presenz — AI Memory Companion",
  description: "A private AI memory companion built from what they actually said.",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
      </head>
      <body>
        <div className="shell">
          <header className="topbar">
            <Link className="brand" href="/">Presenz</Link>
            <nav className="navlinks">
              <Link className="ghostButton" href="/dashboard">Dashboard</Link>
              <Link className="ghostButton" href="/settings">Settings</Link>
              <Link className="button" href="/signup">Sign Up</Link>
            </nav>
          </header>
          {children}
        </div>
      </body>
    </html>
  );
}
