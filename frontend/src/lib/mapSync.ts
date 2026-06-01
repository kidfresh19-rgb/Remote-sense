import type { Map as MaplibreMap } from "maplibre-gl";

interface Camera {
  center: [number, number];
  zoom: number;
  bearing: number;
  pitch: number;
}

function cameraOf(map: MaplibreMap): Camera {
  const c = map.getCenter();
  return {
    center: [c.lng, c.lat],
    zoom: map.getZoom(),
    bearing: map.getBearing(),
    pitch: map.getPitch(),
  };
}

/** Keep two or more MapLibre maps on a shared camera: any move on one mirrors to the others, so a
 *  side-by-side comparison pans and zooms as a single view. Returns a disposer that detaches every
 *  listener. The `applying` guard breaks the feedback loop where a mirrored `jumpTo` re-emits
 *  `move`. On wiring, the followers snap to the first map so they start aligned. */
export function syncCameras(maps: MaplibreMap[]): () => void {
  if (maps.length < 2) return () => {};
  let applying = false;
  const offs: Array<() => void> = [];

  for (const src of maps) {
    const handler = () => {
      if (applying) return;
      applying = true;
      const cam = cameraOf(src);
      for (const other of maps) {
        if (other !== src) other.jumpTo(cam);
      }
      applying = false;
    };
    src.on("move", handler);
    offs.push(() => src.off("move", handler));
  }

  applying = true;
  const cam = cameraOf(maps[0]);
  for (let i = 1; i < maps.length; i++) maps[i].jumpTo(cam);
  applying = false;

  return () => {
    for (const off of offs) off();
  };
}
