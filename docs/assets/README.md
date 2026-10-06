# README visual assets

The English and Turkish README pages use an illustrated cover, a team guide,
and screenshots captured from the current local console.

| Asset | Size | Purpose |
|---|---|---|
| `cover-en.svg`, `cover-tr.svg` | 1600 × 900 | Project identity and the choose → preview → past usage flow |
| `team-guide-en.svg`, `team-guide-tr.svg` | 1600 × 760 | Duties, model selection and recorded usage |
| `preferences-en.png`, `preferences-tr.png` | 1600 × 2296 (EN), 1600 × 2300 (TR) | Current planned-team console with chief settings selected |
| `console-en.png`, `console-tr.png` | 1600 × 2039 (EN), 1600 × 2055 (TR) | Current Usage orchestra, recorded models and token shares |
| `ustam-poster-en.png`, `ustam-poster-tr.png` | 1920 × 1080 | Language-specific video posters |
| `ustam-promo-en-40s.mp4`, `ustam-promo-tr-40s.mp4` | 1920 × 1080, 40 seconds | Legacy console introductions with music |
| `ustam-trailer-poster-tr.png` | 1920 × 1080 | Current Turkish trailer cover |
| `ustam-trailer-tr-55s.mp4` | 1920 × 1080, 55 seconds | Turkish trailer with original music and sound effects, no narration |
| `ustam-current-poster-tr.png` | 1920 × 1080 | Current Turkish hub video poster |
| `ustam-current-usage-tr-65s.mp4` | 1920 × 1080, 65 seconds | Current Turkish hub walkthrough with music |

The illustrations reuse this repository's original orchestra character art.
Each SVG embeds its own symbols and gradients; it has no external images,
fonts or scripts. Colors follow the current console: dark burgundy and brown,
warm gold, cream text. The core palette is `#201619`, `#302124`, `#783238`,
`#E5BE7A`, and `#FFF1D9`.

For a related orchestrator repository, change the cover's product label and
translate the model families in the team guide to that product's actual
choices. The stage, characters and three-step composition are reusable.
Console screenshots belong to the Codex edition of Ustam and should be recaptured
from another product rather than relabeled.

Screenshots use the repository's sanitized usage fixture and a temporary,
empty Git project. No actual user session data or project paths appear.
To open the same console for a capture:

```sh
python3 scripts/dashboard.py /path/to/temporary-git-project \
  --sessions tests/fixtures/usage-sanitized --no-browser --port 8871
```

Use a 1600 × 1100 browser viewport, switch to the desired language, and
capture from the top of the page through `.team-builder` in Preferences or
`#orchestra-panel` in Usage, including the Ustam masthead. Expand the viewport
height to include the full capture. Use reduced motion for stable actors.
The example model selection reflects the local catalogue at capture time;
it does not promise account access. Token values are sample historical
records. The promotional videos use illustrative sample data.

The English and Turkish README pages link the Turkish trailer cover to its
55-second MP4. The previous 65-second hub walkthrough remains available.
The legacy console guides retain their matching 40-second introductions.
Poster links use raw.githubusercontent.com.
This opens the original playable/downloadable asset even when GitHub does not
render a video preview. Superseded preview media and the unused old role diagram
were removed so the current asset collection uses the Ustam name.
