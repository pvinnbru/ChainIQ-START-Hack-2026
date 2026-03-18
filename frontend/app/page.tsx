import Hero from "@/components/landing/hero";
import Functionality from "@/components/landing/functionality";

export default function Home() {
  return (
    <div className="max-w-7xl mx-auto px-6 lg:px-16">
      <Hero/>
      <Functionality/>
    </div>
  );
}
