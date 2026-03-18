import Link from "next/link";
import { Button } from "@/components/ui/button";

export default function Hero(){

  return (
    <section className="py-8 md:py-16 lg:py-24 text-center">
        <div className="mx-auto flex max-w-5xl flex-col gap-6">
          <h1 className="text-3xl font-semibold lg:text-6xl">Procurement Platform</h1>
          <p className="text-muted-foreground text-balance lg:text-lg">
            Upload a procurement request and let our AI-powered engine automatically
            validate it against your policy rules, evaluate suppliers, flag budget and
            lead-time issues, and recommend the optimal sourcing path — all in seconds.
          </p>
        </div>
        <div className="mt-10 flex justify-center gap-4">
          <Button asChild size="lg">
            <Link href="/dashboard">Go to Dashboard</Link>
          </Button>
        </div>
    </section>
  );
};
