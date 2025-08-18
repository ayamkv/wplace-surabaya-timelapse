#!/usr/bin/env python3
"""
Surabaya (WIB / UTC+7) pixel-art timelapse generator.

Features:
- Collect daily merged tile images (merged_tiles_YYYYMMDD_HHMMSS.png)
- Pixel-art safe: all scaling uses NEAREST (no blur)
- Optional static map background instead of solid white
- Timestamp overlay (centered near bottom)
- Encodes via ffmpeg (libx264 by default)

Environment variables:
  VIDEO_WIDTH / VIDEO_HEIGHT  Force output resolution (integers)
  DOWNSCALE_FACTOR            Integer divisor applied to detected source size
  VIDEO_CODEC                 libx264 (default), libx265, ffv1, etc.
  CRF                         x264/x265 quality (default 15, 0 = lossless)
  PRESET                      x264/x265 preset (default slow)
  PIX_FMT                     Pixel format (default yuv444p for crisp chroma)
  EXTRA_FFMPEG                Extra ffmpeg args (e.g. "-tune animation")
  KEEP_FRAMES                 Keep intermediate PNG frames if set
  SKIP_LATEST_COPY            Skip creating timelapse/latest.mp4 copy if set
  STATIC_MAP_PATH             Path to static map image (default static/static_map.png)
  MAP_BACKGROUND              If "0" disables static map background
"""

import os
import glob
import logging
import argparse
import tempfile
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from PIL import Image, ImageDraw

# Logging setup
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Constants / defaults
OUTPUT_DIR = "output"
TIMELAPSE_DIR = "timelapse"
DEFAULT_VIDEO_WIDTH = 3000
DEFAULT_VIDEO_HEIGHT = 3000
FPS = 9
BACKGROUND_COLOR = (255, 255, 255)  # Fallback solid background
SURABAYA_TZ = timezone(timedelta(hours=7))  # WIB (UTC+7)
STATIC_MAP_PATH = os.getenv("STATIC_MAP_PATH", os.path.join("static", "static_map.png"))
USE_MAP = os.getenv("MAP_BACKGROUND", "1") != "0"

# Cached background (w,h,img)
_BG_CACHE = None

def load_and_prepare_background(target_w: int, target_h: int) -> Image.Image:
    """Return an RGB background sized to (target_w,target_h).
    Uses static map (cover + crop, NEAREST) if available & enabled; otherwise solid color."""
    global _BG_CACHE
    if USE_MAP:
        if _BG_CACHE and _BG_CACHE[0] == target_w and _BG_CACHE[1] == target_h:
            return _BG_CACHE[2]
        try:
            if os.path.isfile(STATIC_MAP_PATH):
                with Image.open(STATIC_MAP_PATH) as m:
                    mw, mh = m.size
                    # Cover scaling so map fills frame
                    scale = max(target_w / mw, target_h / mh)
                    new_w = int(mw * scale)
                    new_h = int(mh * scale)
                    resized = m.resize((new_w, new_h), Image.NEAREST)
                    x0 = (new_w - target_w) // 2
                    y0 = (new_h - target_h) // 2
                    cropped = resized.crop((x0, y0, x0 + target_w, y0 + target_h))
                    bg = cropped.convert('RGB')
                    _BG_CACHE = (target_w, target_h, bg)
                    return bg
            else:
                logger.debug(f"Static map path not found: {STATIC_MAP_PATH}")
        except Exception as e:
            logger.warning(f"Static map load failed, fallback solid: {e}")
    # Solid fallback
    return Image.new('RGB', (target_w, target_h), BACKGROUND_COLOR)

# ---------------- Collect images -----------------

def get_images_for_date(date_str):
    """Return sorted list of image paths for given date (YYYYMMDD)."""
    folder = os.path.join(OUTPUT_DIR, date_str)
    if not os.path.isdir(folder):
        logger.warning(f"No folder for date {date_str}: {folder}")
        return []
    images = glob.glob(os.path.join(folder, "merged_tiles_*.png"))

    def key(p):
        fname = os.path.basename(p)
        parts = fname.split('_')
        # merged_tiles_YYYYMMDD_HHMMSS.png -> parts[2]=date, parts[3]=time.png
        if len(parts) >= 4:
            d = parts[2]
            t = parts[3].split('.')[0]
            return f"{d}_{t}"
        return fname

    images.sort(key=key)
    logger.info(f"Found {len(images)} images for {date_str}")
    return images

# ---------------- Pixel-art resizing -----------------

def resize_image_to_fit(image, target_width, target_height):
    """Resize preserving aspect ratio with NEAREST.
    Returns an RGBA canvas (transparent) containing the centered resized image and placement tuple."""
    w, h = image.size
    if (w, h) == (target_width, target_height):
        base = Image.new('RGBA', (target_width, target_height), (0, 0, 0, 0))
        img_rgba = image.convert('RGBA') if image.mode != 'RGBA' else image
        base.paste(img_rgba, (0, 0), img_rgba)
        return base, (0, 0, w, h)
    scale = min(target_width / w, target_height / h)
    new_w = int(w * scale)
    new_h = int(h * scale)
    resized = image.resize((new_w, new_h), Image.NEAREST)
    if resized.mode != 'RGBA':
        resized = resized.convert('RGBA')
    canvas = Image.new('RGBA', (target_width, target_height), (0, 0, 0, 0))
    x = (target_width - new_w) // 2
    y = (target_height - new_h) // 2
    canvas.paste(resized, (x, y), resized)
    return canvas, (x, y, new_w, new_h)

# ---------------- Timestamp overlay -----------------

def add_timestamp_overlay(image, timestamp):
    base = image.convert('RGBA')
    overlay = Image.new('RGBA', base.size, (255, 255, 255, 0))
    draw = ImageDraw.Draw(overlay)
    bbox = draw.textbbox((0, 0), timestamp)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    y = image.height - th - 20
    x = (image.width - tw) // 2
    draw.text((x, y), timestamp, fill=(255, 255, 255, 230), stroke_width=2, stroke_fill=(0, 0, 0, 160))
    return Image.alpha_composite(base, overlay).convert('RGB')

# ---------------- Size determination -----------------

def determine_video_size(images):
    env_w = os.getenv('VIDEO_WIDTH')
    env_h = os.getenv('VIDEO_HEIGHT')
    if env_w and env_h:
        try:
            w = int(env_w); h = int(env_h)
            logger.info(f"Using forced video size {w}x{h}")
            return w, h
        except ValueError:
            logger.warning("Invalid VIDEO_WIDTH/VIDEO_HEIGHT; ignoring.")
    if images:
        try:
            with Image.open(images[0]) as im0:
                sw, sh = im0.size
            ds = os.getenv('DOWNSCALE_FACTOR')
            if ds:
                try:
                    f = int(ds)
                    if f > 1 and sw % f == 0 and sh % f == 0:
                        logger.info(f"Downscale factor {f}: {sw}x{sh} -> {sw//f}x{sh//f}")
                        return sw // f, sh // f
                    else:
                        logger.warning("DOWNSCALE_FACTOR invalid (not divisor).")
                except ValueError:
                    logger.warning("Invalid DOWNSCALE_FACTOR; ignoring.")
            if sw >= 4000 and sw % 2 == 0 and sh % 2 == 0:
                logger.info(f"Auto half-size {sw}x{sh} -> {sw//2}x{sh//2}")
                return sw // 2, sh // 2
            logger.info(f"Using original size {sw}x{sh}")
            return sw, sh
        except Exception as e:
            logger.warning(f"Size inspect failed: {e}")
    logger.info(f"Fallback default {DEFAULT_VIDEO_WIDTH}x{DEFAULT_VIDEO_HEIGHT}")
    return DEFAULT_VIDEO_WIDTH, DEFAULT_VIDEO_HEIGHT

# ---------------- Timestamp parsing -----------------

def build_timestamp(filename, index):
    parts = filename.split('_')
    if len(parts) >= 4:
        d = parts[2]
        t = parts[3].split('.')[0]
        if len(d) == 8 and len(t) == 6:
            return f"{d[:4]}-{d[4:6]}-{d[6:8]} {t[:2]}:{t[2:4]}:{t[4:6]}"
    return f"Frame {index+1}"

# ---------------- ffmpeg encoding -----------------

def encode_with_ffmpeg(frame_dir, pattern, out_path):
    codec = os.getenv('VIDEO_CODEC', 'libx264')
    crf = os.getenv('CRF', '15')
    preset = os.getenv('PRESET', 'slow')
    pix_fmt = os.getenv('PIX_FMT', 'yuv444p')
    extra = os.getenv('EXTRA_FFMPEG', '')
    extra_args = extra.split() if extra else []

    cmd = [
        'ffmpeg', '-y',
        '-framerate', str(FPS),
        '-f', 'image2',
        '-i', os.path.join(frame_dir, pattern),
        '-c:v', codec,
        '-preset', preset,
        '-crf', crf,
        '-pix_fmt', pix_fmt,
        *extra_args,
        '-movflags', '+faststart',
        out_path
    ]

    logger.info('Running ffmpeg: ' + ' '.join(cmd))
    try:
        proc = subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        logger.info('ffmpeg encode complete')
        return True
    except FileNotFoundError:
        logger.error('ffmpeg not found in PATH')
    except subprocess.CalledProcessError as e:
        logger.error(f'ffmpeg failed code={e.returncode}')
        stderr_tail = e.stderr.decode(errors='ignore')[-4000:]
        logger.error(stderr_tail)
    return False

# ---------------- Timelapse creation -----------------

def create_timelapse_video(images, output_path, video_width, video_height):
    if not images:
        logger.error('No images to create timelapse')
        return False

    temp_dir = tempfile.mkdtemp(prefix='frames_')
    logger.info(f'Generating frames in {temp_dir}')

    bg = load_and_prepare_background(video_width, video_height)

    for i, path in enumerate(images):
        try:
            with Image.open(path) as im:
                ts = build_timestamp(os.path.basename(path), i)
                fitted_rgba, _ = resize_image_to_fit(im, video_width, video_height)
                # Composite over background
                frame_rgba = bg.convert('RGBA')
                frame_rgba.alpha_composite(fitted_rgba)
                final = add_timestamp_overlay(frame_rgba.convert('RGB'), ts)
                final.save(os.path.join(temp_dir, f'frame_{i:05d}.png'), 'PNG', optimize=False)
            if (i + 1) % 20 == 0:
                logger.info(f'{i+1}/{len(images)} frames prepared')
        except Exception as e:
            logger.error(f'Frame {path} failed: {e}')

    ok = encode_with_ffmpeg(temp_dir, 'frame_%05d.png', output_path)
    if ok:
        if os.getenv('KEEP_FRAMES'):
            logger.info(f'Frames kept at {temp_dir}')
        else:
            shutil.rmtree(temp_dir, ignore_errors=True)
    else:
        logger.warning(f'Keeping frames for debugging: {temp_dir}')
    return ok

# ---------------- CLI -----------------

def parse_args():
    p = argparse.ArgumentParser(description='Create Surabaya pixel-art timelapse (ffmpeg/x264).')
    p.add_argument('--date', dest='date_str', help='Date YYYYMMDD (default: yesterday UTC+7)')
    return p.parse_args()

# ---------------- Main -----------------

def main():
    os.makedirs(TIMELAPSE_DIR, exist_ok=True)
    args = parse_args()
    if args.date_str:
        date_str = args.date_str
    else:
        date_str = (datetime.now(SURABAYA_TZ) - timedelta(days=1)).strftime('%Y%m%d')

    logger.info(f'Creating timelapse for {date_str}')
    images = get_images_for_date(date_str)
    if not images:
        logger.warning(f'No images found for {date_str}')
        return False

    vid_w, vid_h = determine_video_size(images)
    output_path = os.path.join(TIMELAPSE_DIR, f'timelapse_{date_str}.mp4')

    if create_timelapse_video(images, output_path, vid_w, vid_h):
        logger.info(f'Timelapse created: {output_path}')
        if not os.getenv('SKIP_LATEST_COPY'):
            latest = os.path.join(TIMELAPSE_DIR, 'latest.mp4')
            try:
                if os.path.exists(latest):
                    os.remove(latest)
                shutil.copy2(output_path, latest)
                logger.info('Updated latest.mp4')
            except Exception as e:
                logger.warning(f'Failed to update latest.mp4: {e}')
        return True
    logger.error('Failed to create timelapse')
    return False

if __name__ == '__main__':
    import sys
    sys.exit(0 if main() else 1)
