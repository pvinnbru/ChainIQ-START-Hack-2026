"use client"

import * as React from "react"
import { useEffect, useState } from "react"
import { Command, FileCheck, Bell, LogOut } from "lucide-react"
import Link from "next/link"
import { NavMain } from "@/components/sidebar/nav-main"
import { useAuth } from "@/context/auth-context"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
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

const ROLE_LABELS: Record<string, string> = {
  requester: "Requester",
  approver: "Procurement Manager",
  category_head: "Category Head",
  compliance_reviewer: "Compliance Reviewer",
}

const ROLE_COLORS: Record<string, string> = {
  requester: "text-blue-700 border-blue-300 bg-blue-50",
  approver: "text-emerald-700 border-emerald-300 bg-emerald-50",
  category_head: "text-purple-700 border-purple-300 bg-purple-50",
  compliance_reviewer: "text-amber-700 border-amber-300 bg-amber-50",
}

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"

export function AppSidebar({ ...props }: React.ComponentProps<typeof Sidebar>) {
  const { user, logout } = useAuth()
  const [escalationCount, setEscalationCount] = useState(0)

  useEffect(() => {
    if (!user) return
    fetch(`${API}/escalations/me`, { credentials: "include" })
      .then((r) => r.json())
      .then((data: unknown[]) => setEscalationCount(data.length))
      .catch(() => {})
  }, [user])

  const navMain = [
    {
      title: "Requests",
      icon: FileCheck,
      isActive: true,
      items: [
        { title: "New Request", url: "/dashboard/create" },
        { title: "All Cases", url: "/dashboard/cases" },
      ],
    },
  ]

  // Add "My Escalations" for non-approver roles
  if (user && user.role !== "approver") {
    navMain.push({
      title: "My Escalations",
      icon: Bell,
      isActive: false,
      items: [
        {
          title: escalationCount > 0 ? `Pending (${escalationCount})` : "No pending",
          url: "/dashboard/escalations",
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

      <SidebarSeparator />

      <SidebarFooter className="p-3 space-y-2">
        {user && (
          <>
            <div className="flex items-center gap-2 px-1">
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium truncate">{user.name}</p>
                <Badge
                  variant="outline"
                  className={`text-[10px] mt-0.5 ${ROLE_COLORS[user.role] ?? ""}`}
                >
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
            <Button
              variant="ghost"
              size="sm"
              className="w-full justify-start gap-2 text-muted-foreground hover:text-foreground"
              onClick={logout}
            >
              <LogOut className="h-4 w-4" />
              Sign out
            </Button>
          </>
        )}
      </SidebarFooter>

      <SidebarRail />
    </Sidebar>
  )
}
