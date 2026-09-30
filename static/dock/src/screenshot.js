// Capture rendered pixels, including cross-origin iframes. Never fall back to an uncropped screen.
export function canCapture(view) {
  return !!(view?.navigator.mediaDevices?.getDisplayMedia && view.CropTarget?.fromElement
    && view.BrowserCaptureMediaStreamTrack?.prototype.cropTo && view.ImageCapture);
}

export const afterPaint = (view, signal) => new Promise((resolve, reject) => {
  let frame;
  const finish = (error) => {
    view.cancelAnimationFrame(frame);
    clearTimeout(timer);
    signal?.removeEventListener('abort', aborted);
    error ? reject(error) : resolve();
  };
  const aborted = () => finish(signal.reason);
  const timer = setTimeout(() => finish(new Error('Keep the panel window visible while capturing.')), 8000);
  if (signal?.aborted) { aborted(); return; }
  signal?.addEventListener('abort', aborted, { once: true });
  frame = view.requestAnimationFrame(() => { frame = view.requestAnimationFrame(() => finish()); });
});

export async function capturePanel(el, signal) {
  const doc = el.ownerDocument;
  const view = doc.defaultView;
  if (!canCapture(view)) throw new Error('Panel capture is not supported by this browser.');
  // Start the picker in the click's activation, before waiting for paint or CropTarget.
  let stream;
  let bitmap;
  const stop = () => stream?.getTracks().forEach((track) => track.stop());
  try {
    stream = await view.navigator.mediaDevices.getDisplayMedia({
      video: { displaySurface: 'browser', width: { ideal: view.innerWidth * view.devicePixelRatio },
        height: { ideal: view.innerHeight * view.devicePixelRatio } },
      audio: false, preferCurrentTab: true, selfBrowserSurface: 'include', surfaceSwitching: 'exclude',
    });
    signal.addEventListener('abort', stop, { once: true });
    signal.throwIfAborted();
    const track = stream.getVideoTracks()[0];
    if (!track?.cropTo || track.getSettings().displaySurface !== 'browser') {
      throw new Error('Choose this browser tab in the sharing prompt.');
    }
    await afterPaint(view, signal);
    const target = await view.CropTarget.fromElement(el);
    // Static panels still need a fresh frame after cropping; Chrome otherwise suppresses unchanged frames.
    await track.applyConstraints({ frameRate: { min: 1, ideal: 30 } });
    await track.cropTo(target);
    signal.throwIfAborted();
    const rect = el.getBoundingClientRect();
    if (!el.isConnected || !rect.width || !rect.height) throw new Error('The panel is no longer visible.');
    // Pull a fresh frame after cropTo resolves. A detached video's cached frame can still
    // predate the crop, and waiting for a video callback can stall on a static panel.
    bitmap = await new Promise((resolve, reject) => {
      let done = false;
      const finish = (error, frame) => {
        if (done) { frame?.close(); return; }
        done = true;
        clearTimeout(timer);
        signal.removeEventListener('abort', aborted);
        track.removeEventListener('ended', ended);
        error ? reject(error) : resolve(frame);
      };
      const aborted = () => finish(signal.reason);
      const ended = () => finish(new Error('Screen sharing ended before a frame was captured.'));
      const timer = setTimeout(() => finish(new Error('No panel frame arrived. Choose this tab and keep the panel visible.')), 8000);
      signal.addEventListener('abort', aborted, { once: true });
      track.addEventListener('ended', ended, { once: true });
      const capture = new view.ImageCapture(track);
      // A newly started tab capture can deliver a compositor startup frame with only
      // the background. Discard it and pull the next frame before encoding the PNG.
      capture.grabFrame().then((first) => {
        first.close();
        signal.throwIfAborted();
        if (done) throw new Error('Capture already ended.');
        return capture.grabFrame();
      }).then((frame) => finish(null, frame),
        (error) => finish(error || new Error('No panel frame arrived.')));
    });
    signal.throwIfAborted();
    const canvas = doc.createElement('canvas');
    canvas.width = Math.max(1, Math.round(rect.width * view.devicePixelRatio));
    canvas.height = Math.max(1, Math.round(rect.height * view.devicePixelRatio));
    canvas.getContext('2d').drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    return await new Promise((resolve, reject) => canvas.toBlob((blob) =>
      blob ? resolve(blob) : reject(new Error('Could not encode the screenshot.')), 'image/png'));
  } finally {
    signal.removeEventListener('abort', stop);
    stop();
    bitmap?.close();
  }
}

export function writePng(view, blob) {
  try {
    if (!view.navigator.clipboard?.write || !view.ClipboardItem) throw new Error('Image clipboard access is unavailable.');
    return view.navigator.clipboard.write([new view.ClipboardItem({ 'image/png': blob })]);
  } catch (error) { return Promise.reject(error); }
}
