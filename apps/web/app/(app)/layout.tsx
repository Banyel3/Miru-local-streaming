import { AppShell } from "@/components/Sidebar";
import { MediaFile, getLibrary } from "@/lib/api";

/** The shell owns the sidebar so it survives navigation. The player route sits
 *  outside this group deliberately — it is edge-to-edge video, no chrome. */
export default async function AppLayout({ children }: { children: React.ReactNode }) {
  // Continue Watching needs titles for the ids the browser remembers. If the
  // API is down the shell still renders; the page below reports the outage.
  let files: MediaFile[] = [];
  try {
    files = await getLibrary();
  } catch {
    files = [];
  }

  return <AppShell files={files}>{children}</AppShell>;
}
