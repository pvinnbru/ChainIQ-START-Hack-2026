"use client"

import { ChevronRight, type LucideIcon } from "lucide-react"
import Link from "next/link"
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import { Badge } from "@/components/ui/badge"
import {
  SidebarGroup,
  SidebarGroupLabel,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarMenuSub,
  SidebarMenuSubButton,
  SidebarMenuSubItem,
} from "@/components/ui/sidebar"
export function NavMain({
  items,
}: {
  items: {
    title: string
    icon?: LucideIcon
    isActive?: boolean
    badge?: number | string
    items?: {
      title: string
      url: string
      badge?: number | string
    }[]
  }[]
}) {
  return (
    <SidebarGroup>
      <SidebarGroupLabel>Platform</SidebarGroupLabel>
      <SidebarMenu>
        {items.map((item) => (
          <Collapsible
            key={item.title}
            asChild
            defaultOpen={item.isActive}
            className="group/collapsible"
          >
            <SidebarMenuItem >
              <SidebarMenuButton tooltip={item.title}
                asChild
                className="group/label text-sidebar-foreground hover:bg-sidebar-accent hover:text-sidebar-accent-foreground text-sm hover:cursor-pointer"
              >
                <CollapsibleTrigger>
                  {item.icon && <item.icon className="mr-2 h-4 w-4 shrink-0" />}
                  <span className="flex-1 text-left">{item.title}</span>
                  {item.badge ? (
                    <Badge className="mr-2 h-5 px-1.5 text-[10px] bg-orange-500 hover:bg-orange-600 text-white shrink-0">
                      {item.badge}
                    </Badge>
                  ) : null}
                  <ChevronRight className="transition-transform group-data-[state=open]/collapsible:rotate-90 shrink-0" />
                </CollapsibleTrigger>
              </SidebarMenuButton>
              <CollapsibleContent>
                <SidebarMenuSub>
                  {item.items?.map((subItem) => (
                    <SidebarMenuSubItem key={subItem.title}>
                      <SidebarMenuSubButton asChild>
                        <Link href={subItem.url} className="flex flex-1 items-center justify-between">
                          <span>{subItem.title}</span>
                          {subItem.badge ? (
                            <Badge className="h-5 px-1.5 text-[10px] bg-orange-500 hover:bg-orange-600 text-white shrink-0">
                              {subItem.badge}
                            </Badge>
                          ) : null}
                        </Link>
                      </SidebarMenuSubButton>
                    </SidebarMenuSubItem>
                  ))}
                </SidebarMenuSub>
              </CollapsibleContent>
            </SidebarMenuItem>
          </Collapsible>
        ))}
      </SidebarMenu>
    </SidebarGroup>
  )
}
