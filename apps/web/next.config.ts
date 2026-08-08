import type { NextConfig } from "next";

/** Every origin this app is legitimately reached on.
 *
 *  Server Actions are POSTed to the same origin the page came from, and Next
 *  refuses one whose `Origin` does not match the host it believes it is
 *  serving. Behind nginx it believes wrong: the proxy hands it
 *  `Host: 127.0.0.1:3001`, so a real request from `ban-1.tail88f195.ts.net`
 *  reads as forged and is aborted before any of our code runs. The symptom is
 *  every card saying "Couldn't load the releases for this" while the API is
 *  perfectly healthy.
 *
 *  The nginx side is fixed too — see deploy/nginx/miru.conf, where a location
 *  block silently dropped the server-level `Host` header. This list is the
 *  second lock, and the one that does not need a root-owned file edited to take
 *  effect. Naming the origins is not a weakening: it is the allowlist the check
 *  is asking for, and it is exactly the tailnet names this instance answers on.
 */
const ORIGINS = [
  "ban-1.tail88f195.ts.net",
  "100.71.150.101",
  "localhost:3001",
  "127.0.0.1:3001",
];

const config: NextConfig = {
  // Library data changes only when a scan runs, but "only when a scan runs"
  // includes "just now", so nothing is cached at M1.
  experimental: {
    optimizePackageImports: ["@vidstack/react"],
    serverActions: { allowedOrigins: ORIGINS },
  },
};

export default config;
