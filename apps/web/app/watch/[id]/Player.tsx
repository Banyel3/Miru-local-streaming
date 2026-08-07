"use client";

import { MediaPlayer, MediaProvider } from "@vidstack/react";
import {
  DefaultVideoLayout,
  defaultLayoutIcons,
} from "@vidstack/react/player/layouts/default";
import "@vidstack/react/player/styles/default/theme.css";
import "@vidstack/react/player/styles/default/layouts/video.css";

export function Player({ src, title }: { src: string; title: string }) {
  return (
    <MediaPlayer
      className="h-full w-full"
      title={title}
      src={{ src, type: "video/object" }}
      crossOrigin
      playsInline
      load="eager"
    >
      <MediaProvider />
      {/* Vidstack's shipped layout, repainted via CSS vars in globals.css.
          Spec §10: do not hand-roll video controls. */}
      <DefaultVideoLayout icons={defaultLayoutIcons} />
    </MediaPlayer>
  );
}
