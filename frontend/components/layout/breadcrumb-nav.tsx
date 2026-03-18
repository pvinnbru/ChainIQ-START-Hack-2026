"use client"

import { usePathname } from "next/navigation"
import { Home } from "lucide-react"
import Link from "next/link"
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb"

// UUID validation regex
const UUID_REGEX = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

// Check if a string is a UUID
function isUUID(str: string): boolean {
  return UUID_REGEX.test(str)
}

// Format segment: capitalize first letter, rest lowercase
function formatSegment(segment: string): string {
  if (isUUID(segment)) {
    return "[ID]"
  }
  
  // Decode URI component and replace hyphens/underscores with spaces
  const decoded = decodeURIComponent(segment)
  const cleaned = decoded.replace(/[-_]/g, " ")
  
  // Capitalize first letter, rest lowercase
  return cleaned.charAt(0).toUpperCase() + cleaned.slice(1).toLowerCase()
}

export function BreadcrumbNav() {
  const pathname = usePathname()

  // Split pathname and filter out empty segments
  const segments = pathname.split("/").filter((segment) => segment !== "")

  // If we're at root or just /dashboard, show only Dashboard
  if (segments.length === 0 || (segments.length === 1 && segments[0] === "dashboard")) {
    return (
      <Breadcrumb>
        <BreadcrumbList>
          <BreadcrumbItem>
            <BreadcrumbPage className="flex items-center gap-1.5">
              <Home className="size-4" />
              <span>Dashboard</span>
            </BreadcrumbPage>
          </BreadcrumbItem>
        </BreadcrumbList>
      </Breadcrumb>
    )
  }

  // Build breadcrumb items
  return (
    <Breadcrumb>
      <BreadcrumbList>
        {/* Dashboard home link */}
        <BreadcrumbItem className="hidden md:block">
          <BreadcrumbLink asChild>
            <Link href="/dashboard" className="flex items-center gap-1.5">
              <Home className="size-4" />
              <span>Dashboard</span>
            </Link>
          </BreadcrumbLink>
        </BreadcrumbItem>

        {/* Dynamic segments */}
        {segments.map((segment, index) => {
          // Skip "dashboard" segment as it's already shown as home
          if (segment === "dashboard") return null

          // Build the path up to this segment
          const href = "/" + segments.slice(0, index + 1).join("/")
          const isLast = index === segments.length - 1
          const label = formatSegment(segment)

          return (
            <div key={href} className="contents">
              <BreadcrumbSeparator className="hidden md:block" />
              <BreadcrumbItem className={isLast ? "" : "hidden md:block"}>
                {isLast ? (
                  <BreadcrumbPage>{label}</BreadcrumbPage>
                ) : (
                  <BreadcrumbLink asChild>
                    <Link href={href}>{label}</Link>
                  </BreadcrumbLink>
                )}
              </BreadcrumbItem>
            </div>
          )
        })}
      </BreadcrumbList>
    </Breadcrumb>
  )
}