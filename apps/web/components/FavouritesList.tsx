"use client";

import { MediaFile } from "@/lib/api";
import { useFavourites } from "@/lib/store";
import { MediaGrid } from "@/components/MediaCard";
import { GridSkeleton } from "@/components/Skeletons";
import { EmptyState } from "@/components/ui";
import { Heart } from "@/components/icons";

/** Favourites live in the browser until M3, so the filter has to happen after
 *  hydration. The skeleton covers that gap rather than flashing an empty state
 *  at someone who has favourites. */
export function FavouritesList({ files }: { files: MediaFile[] }) {
  const favourites = useFavourites();

  if (favourites === null) return <GridSkeleton count={6} />;

  const starred = files.filter((f) => favourites.includes(f.id));

  if (starred.length === 0) {
    return (
      <EmptyState title="No favourites yet">
        <p className="flex flex-wrap items-center gap-1.5">
          Hover a poster and press the
          <Heart className="inline size-4 text-accent" />
          to keep it here. Saved on this device until progress tracking moves to the server.
        </p>
      </EmptyState>
    );
  }

  return <MediaGrid files={starred} />;
}
