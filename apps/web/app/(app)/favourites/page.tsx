import { ApiDown } from "@/components/ApiDown";
import { FavouritesList } from "@/components/FavouritesList";
import { SectionHeading } from "@/components/ui";
import { MediaFile, getLibrary } from "@/lib/api";

export const metadata = { title: "Favourites — Miru" };

export default async function FavouritesPage() {
  let files: MediaFile[];
  try {
    files = await getLibrary({ sort: "title" });
  } catch {
    return <ApiDown />;
  }

  return (
    <div className="flex flex-col gap-6">
      <SectionHeading title="Favourites" jp="おきにいり" />
      <FavouritesList files={files} />
    </div>
  );
}
