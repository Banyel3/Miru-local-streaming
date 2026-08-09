"use client";

import { useRouter, useSearchParams } from "next/navigation";

/**
 * Search filters, as URL state.
 *
 * `Barcelona` is a Filipino film, and searching it returned every release
 * whose name contains the word. Relevance ranking puts the film named by the
 * query first; these narrow the rest. URL state, not component state, so a
 * filtered search is shareable and survives reload — and the server does the
 * filtering, because it holds the parser and the category map.
 */
const KINDS = [
  ["", "Any type"],
  ["anime", "Anime"],
  ["movie", "Movies"],
  ["series", "Series"],
] as const;

const QUALITIES = [
  ["", "Any quality"],
  ["2160p", "2160p"],
  ["1080p", "1080p"],
  ["720p", "720p"],
  ["480p", "480p"],
] as const;

const SIZES = [
  ["", "Any size"],
  ["2", "≤ 2 GB"],
  ["5", "≤ 5 GB"],
  ["15", "≤ 15 GB"],
  ["50", "≤ 50 GB"],
] as const;

function Select({
  value,
  onChange,
  options,
  label,
}: {
  value: string;
  onChange: (v: string) => void;
  options: readonly (readonly [string, string])[];
  label: string;
}) {
  return (
    <select
      aria-label={label}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="min-h-11 rounded-full border border-border bg-bg px-3.5 text-[12.5px] font-bold text-text-dim transition-colors hover:border-border-hover"
    >
      {options.map(([v, text]) => (
        <option key={v} value={v}>
          {text}
        </option>
      ))}
    </select>
  );
}

export function SearchFilterRow({
  kind,
  quality,
  maxSizeGb,
}: {
  kind?: string;
  quality?: string;
  maxSizeGb?: string;
}) {
  const router = useRouter();
  const params = useSearchParams();

  const set = (key: string, value: string) => {
    const next = new URLSearchParams(params.toString());
    if (value) next.set(key, value);
    else next.delete(key);
    router.replace(`/search?${next.toString()}`);
  };

  return (
    <div className="flex flex-wrap items-center gap-2">
      <Select label="Type" value={kind ?? ""} onChange={(v) => set("kind", v)} options={KINDS} />
      <Select
        label="Quality"
        value={quality ?? ""}
        onChange={(v) => set("quality", v)}
        options={QUALITIES}
      />
      <Select
        label="Max size"
        value={maxSizeGb ?? ""}
        onChange={(v) => set("max_size_gb", v)}
        options={SIZES}
      />
    </div>
  );
}
