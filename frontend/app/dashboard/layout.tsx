'use client';

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { useAuth } from '@/context/auth-context';
import { EscalationCountProvider } from '@/context/escalation-count-context';
import { AppSidebar } from '@/components/sidebar/app-sidebar';
import { BreadcrumbNav } from '@/components/layout/breadcrumb-nav';
import { Separator } from '@/components/ui/separator';
import {
  SidebarInset,
  SidebarProvider,
  SidebarTrigger,
} from '@/components/ui/sidebar';

export default function Layout({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!loading && !user) {
      router.replace('/login');
    }
  }, [user, loading, router]);

  if (loading || !user) {
    return (
      <div className="flex h-screen w-full items-center justify-center">
        <div className="h-10 w-10 animate-spin rounded-full border-b-2 border-primary" />
      </div>
    );
  }

  return (
    <EscalationCountProvider>
    <SidebarProvider>
      <AppSidebar className="print:hidden" />
      <SidebarInset className="flex flex-col h-screen overflow-hidden print:h-auto print:overflow-visible">
        <header className="sticky top-0 z-10 flex h-16 shrink-0 items-center gap-2 bg-background border-b transition-[width,height] ease-linear group-has-data-[collapsible=icon]/sidebar-wrapper:h-12 print:hidden">
          <div className="flex items-center gap-2 px-4">
            <SidebarTrigger className="-ml-1" />
            <Separator
              orientation="vertical"
              className="mr-2 data-[orientation=vertical]:h-4"
            />
            <BreadcrumbNav />
          </div>
        </header>
        <div className="flex flex-1 flex-col gap-4 p-4 pt-0 overflow-auto print:overflow-visible print:h-auto print:p-0">
          {children}
        </div>
      </SidebarInset>
    </SidebarProvider>
    </EscalationCountProvider>
  );
}
