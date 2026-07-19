/** Per-task copy for the working screen + preview chips (plain language everywhere). */
export const TASK_META = {
  remove: {
    working: { title: 'Rebuilding what’s under the marks', steps: ['Reading every frame', 'Recovering the real footage underneath', 'Cleaning leftover traces', 'Sharpness check on faces'] }, note: 'Faces are protected the whole way — they’re never repainted.',
    badge: (n) => (n > 1 ? 'Both removed' : 'Removed'),
    chips: (labels) => [...new Set(labels.map(l => l.replace(/ \d+$/, '')))].map(l => `${l} gone`),
    showBefore: true,
  },
  erase: {
    working: { title: 'Erasing what you marked', steps: ['Reading every frame', 'Following the marked spots', 'Filling in real background', 'Double-checking the result'] }, note: 'Only the spots you marked are touched — nothing else changes.',
    badge: (n) => (n > 1 ? 'Both erased' : 'Erased'),
    chips: (labels) => [...new Set(labels.map(l => l.replace(/ \d+$/, '')))].map(l => `${l} gone`),
    showBefore: true,
  },
  enhance: {
    working: { title: 'Making every frame sharper', steps: ['Reading every frame', 'Cleaning compression noise', 'Sharpening real detail', 'Keeping faces natural'] }, note: 'Faces are kept natural — nothing gets the plastic look.',
    badge: () => 'Enhanced',
    chips: (_, opts) => [opts.denoise && 'Noise cleaned', opts.sharpen && 'Sharpened', opts.upscale && '2× bigger'].filter(Boolean),
    showBefore: true,
  },
  reframe: {
    working: { title: 'Reframing around your subject', steps: ['Reading every frame', 'Following the subject', 'Reframing each shot', 'Smoothing the camera path'] }, note: 'Nothing is removed or repainted — just reframed.',
    badge: () => 'Reframed',
    chips: (_, opts) => [`Now ${opts.ratio}`, opts.fit !== 'crop' ? 'Soft bars added' : (opts.focus ? 'Focus pinned' : 'Subject followed')],
    showBefore: false,     // aspect changes — a split slider would mislead
  },
  blur: {
    working: { title: 'Hiding faces & plates', steps: ['Reading every frame', 'Finding faces & plates', 'Hiding them smoothly', 'Double-checking every frame'] }, note: 'Only detected faces/plates are obscured — the rest is untouched.',
    badge: (_, qc) => (qc && qc.hidden_frames === 0 ? 'Nothing to hide' : 'Hidden'),
    chips: (_, opts, qc) => {
      if (qc && qc.hidden_frames === 0) return ['Checked every frame — nothing needed hiding']
      return [opts.faces && 'Faces hidden', opts.plates && 'Plates hidden', opts.style === 'pixelate' ? 'Pixelated' : 'Soft blur'].filter(Boolean)
    },
    showBefore: true,
  },
  reel: {
    working: { title: 'Building your reel', steps: ['Cutting to the best part', 'Cropping around the subject', 'Writing the captions', 'Stitching it together'] }, note: 'Your original upload stays untouched — the reel is a new file.',
    badge: () => 'Reel built',
    chips: (_, opts) => [opts.crop && 'Cropped 9:16', opts.captions && 'Captions', opts.endCard && 'End card', opts.cleanAudio && 'Audio cleaned'].filter(Boolean),
    showBefore: false,
  },
  captions: {
    working: { title: 'Writing your captions', steps: ['Listening to the audio', 'Writing the words', 'Styling the captions', 'Burning them in'] }, note: 'The video itself isn’t altered — words are drawn on top.',
    badge: () => 'Captioned',
    chips: () => ['Captions burned in', 'Free .srt included'],
    showBefore: true,
  },
}
