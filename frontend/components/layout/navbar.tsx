"use client";
import { Menu } from "lucide-react";
import Link from "next/link";
import Image from "next/image";
import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import Logo from "@/public/chain-iq-logo-black.svg"
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";

export default function Navbar() {

  const menuItems = [
    { title: "Home", url: "/" },
  ];

  const [open, setOpen] = useState(false);
  const pathname = usePathname();
  useEffect(() => {
    setOpen(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pathname]);

  // Don't render navbar on dashboard pages
  if (pathname.startsWith('/dashboard')) {
    return null;
  }

  return (
    <section className="sticky top-0 z-50 bg-background/80 backdrop-blur border-b py-4">
      <div className="max-w-7xl mx-auto px-6 lg:px-16">
        {/* Desktop Menu */}
        <nav className="hidden justify-between items-center lg:flex">
          <div className="flex items-center gap-6">
            {/* Logo */}
            <Link href="/" className="flex items-center gap-2">
              <Image
                src={Logo}
                width={120}
                height={40}
                alt="Logo"
                className="h-14 w-auto"
              />
            </Link>
            <div className="flex items-center gap-1">
              {menuItems.map((item) => (
                <Link
                  key={item.title}
                  href={item.url}
                  className="hover:bg-muted hover:text-accent-foreground inline-flex h-10 items-center justify-center rounded-md px-4 py-2 text-sm font-medium transition-colors"
                >
                  {item.title}
                </Link>
              ))}
            </div>
          </div>
          <Button asChild size="sm">
            <Link href="/dashboard">Dashboard</Link>
          </Button>
        </nav>

        {/* Mobile Menu */}
        <div className="block lg:hidden">
          <div className="flex items-center justify-between">
            {/* Logo */}
            <Link href="/" className="flex items-center gap-2">
              <Image
                src={Logo}
                width={100}
                height={32}
                alt="Logo"
                className="h-8 w-auto"
              />
            </Link>
            <Sheet open={open} onOpenChange={setOpen}>
              <SheetTrigger asChild>
                <Button variant="outline" size="icon">
                  <Menu className="size-4" />
                </Button>
              </SheetTrigger>
              <SheetContent side="bottom" className="overflow-y-auto">
                <SheetHeader>
                  <SheetTitle>
                    <Link href="/" className="flex items-center gap-2" onClick={() => setOpen(false)}>
                      <Image
                        src={Logo}
                        width={100}
                        height={32}
                        alt="Logo"
                        className="h-8 w-auto"
                      />
                    </Link>
                  </SheetTitle>
                </SheetHeader>
                <div className="flex flex-col gap-6 p-4">
                  <div className="flex flex-col gap-4">
                    {menuItems.map((item) => (
                      <Link
                        key={item.title}
                        href={item.url}
                        className="text-md font-semibold" onClick={() => setOpen(false)}
                      >
                        {item.title}
                      </Link>
                    ))}
                  </div>
                  <Button asChild size="sm">
                    <Link href="/dashboard">Dashboard</Link>
                  </Button>
                </div>
              </SheetContent>
            </Sheet>
          </div>
        </div>
      </div>
    </section>
  );
};
