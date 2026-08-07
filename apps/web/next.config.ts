import type { NextConfig } from "next";

const config: NextConfig = {
  // Library data changes only when a scan runs, but "only when a scan runs"
  // includes "just now", so nothing is cached at M1.
  experimental: { optimizePackageImports: ["@vidstack/react"] },
};

export default config;
