"use client"

import * as React from "react"
import { Command, FileCheck, Bell, LogOut, Sun, Moon } from "lucide-react"
import Link from "next/link"
import { useTheme } from "next-themes"
import { NavMain } from "@/components/sidebar/nav-main"
import { useAuth } from "@/context/auth-context"
import { useEscalationCount } from "@/context/escalation-count-context"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { ROLE_BADGE, ROLE_AVATAR, ROLE_LABELS } from "@/lib/colors"
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
  SidebarSeparator,
} from "@/components/ui/sidebar"


export function AppSidebar({ ...props }: React.ComponentProps<typeof Sidebar>) {
  const { user, logout } = useAuth()
  const { count: escalationCount } = useEscalationCount()
  const { resolvedTheme, setTheme } = useTheme()

  const navMain: {
    title: string
    icon?: any
    isActive?: boolean
    badge?: number | string
    items?: { title: string; url: string; badge?: number | string }[]
  }[] = [
    {
      title: "Requests",
      icon: FileCheck,
      isActive: true,
      items: [
        { title: "Overview", url: "/dashboard" },
        { title: "New Request", url: "/dashboard/create" },
        { title: "All Cases", url: "/dashboard/cases" },
      ],
    },
  ]

  if (user) {
    navMain.push({
      title: "My Escalations",
      icon: Bell,
      isActive: false,
      badge: escalationCount > 0 ? escalationCount : undefined,
      items: [
        {
          title: "All Pending",
          url: "/dashboard/escalations",
          badge: escalationCount > 0 ? escalationCount : undefined,
        },
      ],
    })
  }

  return (
    <Sidebar collapsible="icon" {...props}>
      <SidebarHeader>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton size="lg">
              <div className="bg-sidebar-primary text-sidebar-primary-foreground flex aspect-square size-8 items-center justify-center rounded-lg">
                <Command className="size-4" />
              </div>
              <div className="grid flex-1 text-left text-sm leading-tight">
                <span className="truncate font-medium">ChainIQ</span>
                <span className="truncate text-xs">Procurement Audit</span>
              </div>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>

      <SidebarContent>
        <NavMain items={navMain} />
      </SidebarContent>

      <SidebarSeparator className="group-data-[collapsible=icon]:hidden" />

      <SidebarFooter className="p-2">
        {user && (
          <>
            {/* Expanded: full user row */}
            <div className="group-data-[collapsible=icon]:hidden flex items-center gap-2 px-1 py-1">
              <div className={`h-8 w-8 rounded-full flex items-center justify-center text-xs font-semibold shrink-0 ${ROLE_AVATAR[user.role] ?? "bg-muted text-muted-foreground"}`}>
                {user.name.split(" ").map((n: string) => n[0]).join("").slice(0, 2).toUpperCase()}
              </div>
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium truncate">{user.name}</p>
                <Badge variant="outline" className={`text-[10px] mt-0.5 ${ROLE_BADGE[user.role] ?? ""}`}>
                  {ROLE_LABELS[user.role] ?? user.role}
                </Badge>
              </div>
              {escalationCount > 0 && (
                <Link href="/dashboard/escalations">
                  <Badge className="bg-orange-500 hover:bg-orange-600 text-white text-xs cursor-pointer">
                    {escalationCount}
                  </Badge>
                </Link>
              )}
            </div>

            {/* Expanded: action buttons */}
            <div className="group-data-[collapsible=icon]:hidden flex gap-1">
              <Button
                variant="ghost"
                size="sm"
                className="flex-1 justify-start gap-2 text-muted-foreground hover:text-foreground"
                onClick={logout}
              >
                <LogOut className="h-4 w-4" />
                Sign out
              </Button>
              <Button
                variant="ghost"
                size="sm"
                className="px-2 text-muted-foreground hover:text-foreground"
                onClick={() => setTheme(resolvedTheme === 'dark' ? 'light' : 'dark')}
                title="Toggle theme"
              >
                {resolvedTheme === 'dark' ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
              </Button>
            </div>

            {/* Collapsed: icon-only buttons */}
            <div className="hidden group-data-[collapsible=icon]:flex flex-col items-center gap-1">
              <div className="relative">
                <div className={`h-7 w-7 rounded-full flex items-center justify-center text-xs font-semibold ${ROLE_AVATAR[user.role] ?? "bg-muted text-muted-foreground"}`}>
                  {user.name.split(" ").map((n: string) => n[0]).join("").slice(0, 2).toUpperCase()}
                </div>
                {escalationCount > 0 && (
                  <Link href="/dashboard/escalations">
                    <span className="absolute -top-1 -right-1 h-4 w-4 rounded-full bg-orange-500 text-white text-[10px] font-bold flex items-center justify-center leading-none">
                      {escalationCount > 9 ? '9+' : escalationCount}
                    </span>
                  </Link>
                )}
              </div>
              <Button
                variant="ghost"
                size="sm"
                className="h-7 w-7 p-0 text-muted-foreground hover:text-foreground"
                onClick={logout}
                title="Sign out"
              >
                <LogOut className="h-4 w-4" />
              </Button>
              <Button
                variant="ghost"
                size="sm"
                className="h-7 w-7 p-0 text-muted-foreground hover:text-foreground"
                onClick={() => setTheme(resolvedTheme === 'dark' ? 'light' : 'dark')}
                title="Toggle theme"
              >
                {resolvedTheme === 'dark' ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
              </Button>
            </div>
          </>
        )}
      </SidebarFooter>

      <SidebarRail />
    </Sidebar>
  )
}
