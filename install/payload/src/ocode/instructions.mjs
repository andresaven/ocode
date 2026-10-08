import {
  mkdirSync, readFileSync, renameSync, unwatchFile, watchFile, writeFileSync,
} from "node:fs";
import { dirname } from "node:path";

// The source files are read-only mounts outside the working copy. Only the
// effective global instructions are generated, in the container's config dir.
export function syncInstructions({ globalSource, projectSource, target, onError }) {
  const sources = [globalSource, projectSource].filter(Boolean);
  let previous = null;
  function refresh() {
    const contents = sources.map(source =>
      readFileSync(source, "utf8").replace(/^\uFEFF/, ""),
    ).join("\n\n");
    if (contents === previous) return;
    mkdirSync(dirname(target), { recursive: true });
    // Atomic replacement avoids OpenCode reading partially written guidance.
    writeFileSync(`${target}.tmp`, contents, { mode: 0o600 });
    renameSync(`${target}.tmp`, target);
    previous = contents;
  }
  refresh(); // A missing/unreadable source must prevent backend startup.
  const changed = () => {
    try {
      refresh();
    } catch (error) {
      onError(error);
    }
  };
  for (const source of sources) {
    watchFile(source, { interval: 250, persistent: false }, changed);
  }
  return () => {
    for (const source of sources) unwatchFile(source, changed);
  };
}
