# Frontend and design brief

## Brief

RelayLab is for an engineer or hiring reviewer inspecting failure handling on a laptop. The primary task is to follow one delivery from envelope through claim, attempt, backoff, and resolution without an account or setup. The visual direction is a precise dark control ledger: dense but legible, mint for verified progress, amber for recoverable failure, red-orange for terminal rejection, and a restrained monospace evidence layer. It should feel like a deliberately authored operations instrument rather than a generic analytics dashboard.

## Interface

The large opening statement establishes the experiment, followed by queue totals and a single control strip. The delivery ledger remains the main surface. Selecting its native button exposes the canonical payload, hash, signature, state, and replay control. The attempt history preserves every outcome and worker name in reverse chronological order.

The console uses semantic tables, native selects and buttons, `aria-live` action feedback, visible mint focus rings, and minimum 44-pixel primary controls. Layouts collapse to one column below 1000px, and small screens retain horizontal table scrolling instead of compressing evidence into unreadable cells. Motion is minimal and removed for `prefers-reduced-motion`.

Google Fonts are a visual enhancement. The fallback stack keeps the console usable if the font request is unavailable.
