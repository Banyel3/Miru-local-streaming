import { ApiDown } from "@/components/ApiDown";
import { Wall } from "@/components/Wall";
import { MediaFile, Wall as WallData, getLibrary, getWall } from "@/lib/api";

/**
 * Home is the browse wall.
 *
 * `/library` keeps the dense file-facts view it always had; this replaces the
 * poster grid that used to live here, which survives as the "In your library"
 * rail so nothing was lost in the move.
 */
export default async function HomePage({
  searchParams,
}: {
  searchParams: Promise<{ kind?: string }>;
}) {
  const { kind = "all" } = await searchParams;

  // The wall and the library are independent: a dead catalog must not hide the
  // files you already own, and an empty library must not hide the wall.
  const [wall, files] = await Promise.all([
    getWall(kind).catch(() => null),
    getLibrary().catch(() => [] as MediaFile[]),
  ]);

  if (!wall) return <ApiDown />;

  return <Wall data={wall as WallData} files={files} />;
}
