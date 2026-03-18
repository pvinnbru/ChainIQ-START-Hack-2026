"use client"
import Link from "next/link";
import Image from "next/image";
import Logo from "@/public/chain-iq-logo-black.svg"
import { usePathname } from "next/navigation";
export default function Footer() {

  const pathname = usePathname();

  // Don't render footer on dashboard pages
  if (pathname.startsWith('/dashboard')) {
    return null;
  }

  const navLinks = [
    { title: "Home", url: "/" },
    { title: "Dashboard", url: "/dashboard" },
  ];

  return (
    <section className="py-16 border-t">
      <div className="max-w-7xl mx-auto px-6 lg:px-16">
        <footer>
          <div className="grid grid-cols-1 gap-8 md:grid-cols-2">
            {/* Logo Section */}
            <div>
              <Link href="/" className="flex items-center gap-2">
                <Image
                  src={Logo}
                  width={120}
                  height={40}
                  alt="Logo"
                  className="h-14 w-auto"
                />
              </Link>
              <p className="mt-4 text-sm text-muted-foreground">
                ChainIQ — AI-Powered Procurement Audit
              </p>
            </div>

            {/* Navigation Links */}
            <div>
              <h3 className="mb-4 font-semibold">Navigation</h3>
              <ul className="space-y-3 text-sm text-muted-foreground">
                {navLinks.map((link) => (
                  <li key={link.title} className="w-fit hover:text-primary">
                    <Link href={link.url}>{link.title}</Link>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </footer>
      </div>
    </section>
  );
};
