"use client";

import { ShieldCheck, Truck, AlertTriangle, ClipboardList } from "lucide-react";

export default function Functionality(){
  const features = [
    {
      icon: <ShieldCheck className="h-6 w-6" />,
      title: "Policy Validation",
      description:
        "Automatically checks every request against your procurement rulebook — approval thresholds, quote requirements, and budget constraints.",
      items: ["Budget Feasibility", "Approval Thresholds", "Quote Requirements"],
    },
    {
      icon: <Truck className="h-6 w-6" />,
      title: "Supplier Evaluation",
      description:
        "Ranks eligible suppliers by price, quality, risk, and ESG scores. Highlights preferred and incumbent suppliers for quick comparison.",
      items: ["Pricing Tiers", "Quality & Risk Scoring", "Preferred Supplier Match"],
    },
    {
      icon: <AlertTriangle className="h-6 w-6" />,
      title: "Escalation Engine",
      description:
        "Detects blocking issues — budget shortfalls, policy conflicts, infeasible timelines — and routes them to the right approver automatically.",
      items: ["Budget Escalation", "Policy Conflict Detection", "Lead-Time Alerts"],
    },
    {
      icon: <ClipboardList className="h-6 w-6" />,
      title: "Audit Trail",
      description:
        "Full transparency on every decision: which policies were checked, which suppliers were evaluated, and which data sources were used.",
      items: ["Policy Traceability", "Supplier Audit Log", "Data Source Tracking"],
    },
  ];

  return (
    <section className="py-8 md:py-16 lg:py-24">
      <div className="space-y-4 text-center pb-12">
        <h2 className="text-3xl font-semibold tracking-tight md:text-4xl">
          How It Works
        </h2>
        <p className="text-muted-foreground mx-auto max-w-2xl text-lg tracking-tight md:text-xl">
          An AI-powered procurement audit system that validates requests,
          evaluates suppliers, and ensures policy compliance end-to-end.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-8 md:grid-cols-2">
        {features.map((feature, index) => (
          <div
            key={index}
            className="border-border space-y-6 rounded-lg border p-8 transition-shadow hover:shadow-sm"
          >
            <div className="flex items-center gap-4">
              <div className="bg-muted text-primary rounded-full p-3">
                {feature.icon}
              </div>
              <h3 className="text-xl font-semibold">{feature.title}</h3>
            </div>
            <p className="text-muted-foreground leading-relaxed">
              {feature.description}
            </p>
            <div className="space-y-2">
              {feature.items.map((item, itemIndex) => (
                <div key={itemIndex} className="flex items-center gap-2">
                  <div className="bg-foreground h-1.5 w-1.5 rounded-full" />
                  <span className="text-sm font-medium">{item}</span>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
};
