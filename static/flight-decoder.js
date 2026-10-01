/* Decode worker for the scroll fly-through.
 *
 * One job: turn a frame's already-downloaded Blob into an ImageBitmap and
 * hand it back. That is the whole file, and it is a separate file for one
 * reason: createImageBitmap on a 1920x1080 AVIF frame costs about 21ms of
 * real work on a fast laptop, and 34ms at 2560x1440. On the main thread that
 * is two whole screen refreshes at 60Hz, which is exactly how the fly-through
 * used to end up stuck on one frame during a fast fling. In a pool of these,
 * the main thread only ever draws.
 *
 * Blobs cross the postMessage boundary by reference (no byte copy), and the
 * finished ImageBitmap comes back as a transferable, so neither direction
 * copies pixels.
 */

self.onmessage = function (e) {
  var d = e.data;
  if (!d || d.kind !== "decode") return;
  createImageBitmap(d.blob).then(function (bmp) {
    self.postMessage(
      { kind: "decoded", tier: d.tier, index: d.index, bmp: bmp },
      [bmp]
    );
  }).catch(function () {
    // A frame that will not decode is not fatal: the caller falls back to a
    // neighbour, and reporting the failure lets it stop asking for this one.
    self.postMessage({ kind: "failed", tier: d.tier, index: d.index });
  });
};
