# docs

Everything that is not source code lives here.

| Folder | What goes in it |
|--------|-----------------|
| [`assets/`](assets) | Images and media used by the docs and the README (screenshots, banners, the demo GIF). |
| [`devlog/`](devlog) | Social posts / write-ups about the development of Converter, with their caption and HTML. |
| [`releases/`](releases) | Release notes, one file per version (`vX.Y.Z.md`), plus the notes' own README. |

## Media naming

- Screenshots and banners: `screenshot.png`, `banner.png`.
- The app demo animation: `converter-demo.gif` (still frames used as a preview: `promo-still.jpg`).
- Devlog images: `stardance-<what>.png`, kept next to the devlog post that uses them.

## Generated media is not committed

The promo video and its working files are generated locally in `brag-output/`
(gitignored). Only the final, curated artifacts are kept in `assets/`.
