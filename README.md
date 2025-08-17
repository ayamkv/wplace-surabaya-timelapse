# Surabaya Timelapses (Wplace)

Daily pixel‑art timelapse videos from merged Surabaya tile dumps.  
Results saved in `timelapse/`.

## What Happens
1. Another script (different repo) saves images like:
   `output/YYYYMMDD/merged_tiles_YYYYMMDD_HHMMSS.png`
2. This repo’s script (`create_timelapse.py`):
   - Picks a date (default = yesterday, Surabaya time UTC+7)
   - Sorts that day’s images
   - Resizes with NEAREST (no blur), adds timestamp
   - Uses `ffmpeg` (H.264) to make `timelapse/timelapse_YYYYMMDD.mp4`
   - Copies it to `timelapse/latest.mp4` (just the newest day)

`latest.mp4` is ONLY that last day, not all days combined.

## Simple Terms (Environment Settings)
| Variable | Plain meaning | Typical value |
|----------|---------------|---------------|
| DOWNSCALE_FACTOR | Shrink size (must divide width & height) | 2 |
| CRF | Quality number (lower = better/bigger file, 15 good, 20 smaller) | 18–20 |
| PIX_FMT | Color format. `yuv444p` = best edges, `yuv420p` = smaller | yuv444p or yuv420p |
| PRESET | Encode speed vs size | slow |
| VIDEO_WIDTH / VIDEO_HEIGHT | Force exact size (skip auto logic) | (leave unset) |
| VIDEO_CODEC | Usually leave as `libx264` | libx264 |
| EXTRA_FFMPEG | Extra flags (`-tune animation`) | optional |
| KEEP_FRAMES | Keep temp PNG frames (debug) | unset |
| SKIP_LATEST_COPY | Don’t make `latest.mp4` | unset |

Auto size logic (if you don’t force it):
1. If width ≥ 4000 and divisible by 2 → halves it
2. Else keeps original
3. Else fallback 3000×3000

## Stay Under GitHub Actions Limits
Goal: keep each daily video reasonably small (< ~50–80 MB) so:
- Fast to upload as artifact
- Doesn’t eat bandwidth
How:
- Use `DOWNSCALE_FACTOR=2` if source is huge (e.g. 6000×4000 → 3000×2000)
- Use `CRF=18` (sharp) or `CRF=20` (smaller)
- Use `PIX_FMT=yuv420p` if size matters more than perfect color edges

Artifacts (recommended):
- Each workflow run can upload the MP4 as an artifact (auto cleanup after retention period—GitHub default up to 90 days).
- Keep only `latest.mp4` (or nothing) committed to git so the repo stays small.

Avoid committing every day’s MP4 forever (repo bloat).  
Git LFS: not great for daily growing history unless you pay.  
Releases: use only for “bundle” (e.g. weekly or monthly stitched video).

## Run Locally
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate
pip install -r requirements.txt
# Ensure ffmpeg on PATH
python create_timelapse.py              # yesterday
python create_timelapse.py --date 20250816
```

Examples:
```bash
# Smaller file (recommended daily)
DOWNSCALE_FACTOR=2 CRF=20 PIX_FMT=yuv420p python create_timelapse.py
# Higher quality
CRF=15 PIX_FMT=yuv444p python create_timelapse.py
# Lossless (big)
CRF=0 PIX_FMT=yuv444p python create_timelapse.py
```

## Input Folder Example
```
output/
  20250816/
    merged_tiles_20250816_000500.png
    merged_tiles_20250816_001000.png
    ...
```

## Avoid Downloading Videos When Cloning
```bash
git sparse-checkout init --no-cone
git sparse-checkout set "/*" "!/timelapse/"
```
Restore:
```bash
git sparse-checkout disable
```

## Making a Multi‑Day Video Later
Fast (no re-encode, all settings identical):
```bash
printf "file 'timelapse_20250814.mp4'\nfile 'timelapse_20250815.mp4'\n" > list.txt
ffmpeg -f concat -safe 0 -i list.txt -c copy timelapse_20250814-15.mp4
```
If settings differ → re-encode:
```bash
ffmpeg -f concat -safe 0 -i list.txt -c:v libx264 -crf 18 -preset slow -pix_fmt yuv444p all.mp4
```

## FAQ
Q: Does it make one video per day automatically?  
A: Yes (yesterday’s) when the daily workflow runs.

Q: Is `latest.mp4` all days combined?  
A: No. Just the most recent single day.

Q: How do I get an “all time” video?  
A: Concatenate daily MP4s (if identical settings) or re-encode them together.

Q: What should I commit?  
A: Prefer only `latest.mp4` (or nothing). Use artifacts for the rest.
