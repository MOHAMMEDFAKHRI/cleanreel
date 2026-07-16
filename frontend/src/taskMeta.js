/** Per-task copy for the working screen + preview chips (plain language everywhere). */
export const TASK_META = {
  remove: {
    working: { title: 'Rebuilding what’s under the marks', steps: ['Reading every frame', 'Recovering the real footage underneath', 'Cleaning leftover traces', 'Sharpness check on faces'] },
    badge: (n) => (n > 1 ? 'Both removed' : 'Removed'),
    chips: (labels) => [...new Set(labels.map(l => l.replace(/ \d+$/, '')))].map(l => `${l} gone`),
    showBefore: true,
  },
  enhance: {
    working: { title: 'Making every frame sharper', steps: ['Reading every frame', 'Cleaning compression noise', 'Sharpening real detail', 'Keeping faces natural'] },
    badge: () => 'Enhanced',
    chips: (_, opts) => [opts.denoise && 'Noise cleaned', opts.sharpen && 'Sharpened', opts.upscale && '2× bigger'].filter(Boolean),
    showBefore: true,
  },
  reframe: {
    working: { title: 'Reframing around your subject', steps: ['Reading every frame', 'Following the subject', 'Reframing each shot', 'Smoothing the camera path'] },
    badge: () => 'Reframed',
    chips: (_, opts) => [`Now ${opts.ratio}`, opts.fit === 'crop' ? 'Subject followed' : 'Soft bars added'],
    showBefore: false,     // aspect changes — a split slider would mislead
  },
  blur: {
    working: { title: 'Hiding faces & plates', steps: ['Reading every frame', 'Finding faces & plates', 'Hiding them smoothly', 'Double-checking every frame'] },
    badge: () => 'Hidden',
    chips: (_, opts) => [opts.faces && 'Faces hidden', opts.plates && 'Plates hidden', opts.style === 'pixelate' ? 'Pixelated' : 'Soft blur'].filter(Boolean),
    showBefore: true,
  },
  reel: {
    working: { title: 'Building your reel', steps: ['Cutting to the best part', 'Cropping around the subject', 'Writing the captions', 'Stitching it together'] },
    badge: () => 'Reel built',
    chips: (_, opts) => [opts.crop && 'Cropped 9:16', opts.captions && 'Captions', opts.endCard && 'End card', opts.cleanAudio && 'Audio cleaned'].filter(Boolean),
    showBefore: false,
  },
  captions: {
    working: { title: 'Writing your captions', steps: ['Listening to the audio', 'Writing the words', 'Styling the captions', 'Burning them in'] },
    badge: () => 'Captioned',
    chips: () => ['Captions burned in', 'Free .srt included'],
    showBefore: true,
  },
}
