import os
import subprocess
import requests
import tempfile
import json
import math
from flask import Flask, request, jsonify, send_file

app = Flask(__name__)

CLIP_DURATION = 4  # seconds between cuts


@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok"})


@app.route('/assemble', methods=['POST'])
def assemble():
    audio_file = request.files.get('audio')
    video_urls_raw = request.form.get('videoUrls', '[]')
    title = request.form.get('title', 'video')

    if not audio_file:
        return jsonify({"error": f"Missing audio. Got files: {list(request.files.keys())}, form: {list(request.form.keys())}"}), 400

    try:
        video_urls = json.loads(video_urls_raw)
    except Exception:
        return jsonify({"error": f"Invalid videoUrls: {video_urls_raw}"}), 400

    if not video_urls:
        return jsonify({"error": "videoUrls is empty"}), 400

    with tempfile.TemporaryDirectory() as tmpdir:
        audio_path = os.path.join(tmpdir, 'audio.mp3')
        audio_file.save(audio_path)

        # Get audio duration up front so we know how many segments to build
        probe = subprocess.run([
            'ffprobe', '-v', 'error', '-show_entries',
            'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1',
            audio_path
        ], capture_output=True, text=True, check=True)
        total_duration = float(probe.stdout.strip())

        # Download all source clips
        raw_paths = []
        for i, url in enumerate(video_urls):
            raw_path = os.path.join(tmpdir, f'raw_{i}.mp4')
            r = requests.get(url, stream=True, timeout=30)
            r.raise_for_status()
            with open(raw_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
            raw_paths.append(raw_path)

        # Probe each downloaded clip so we can pick sane start offsets when cycling
        clip_durations = []
        for p in raw_paths:
            res = subprocess.run([
                'ffprobe', '-v', 'error', '-show_entries',
                'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', p
            ], capture_output=True, text=True)
            try:
                clip_durations.append(float(res.stdout.strip()))
            except ValueError:
                clip_durations.append(10.0)

        # Build fixed-length segments that cover total_duration.
        # Cycle through clips round-robin; on repeat visits use a later start
        # offset so the footage looks different.
        num_segments = math.ceil(total_duration / CLIP_DURATION)
        segment_paths = []

        for seg_idx in range(num_segments):
            clip_idx = seg_idx % len(raw_paths)
            source = raw_paths[clip_idx]
            clip_dur = clip_durations[clip_idx]

            # How many times have we visited this clip already?
            visits = seg_idx // len(raw_paths)
            # Advance start by CLIP_DURATION per visit, but stay inside the clip
            max_start = max(clip_dur - CLIP_DURATION, 0.0)
            start = (visits * CLIP_DURATION) % (max_start + 0.001) if max_start > 0 else 0.0

            seg_path = os.path.join(tmpdir, f'seg_{seg_idx}.mp4')
            subprocess.run([
                'ffmpeg', '-y',
                '-ss', str(start),
                '-i', source,
                '-t', str(CLIP_DURATION),
                # Scale to fill 1080x1920, then crop to exact dimensions
                '-vf', 'scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920',
                '-c:v', 'libx264', '-preset', 'fast',
                '-r', '30',          # normalise frame rate so concat works cleanly
                '-an',               # strip audio from video track
                seg_path
            ], check=True)
            segment_paths.append(seg_path)

        # Concatenate all pre-encoded, same-format segments
        concat_txt = os.path.join(tmpdir, 'concat.txt')
        with open(concat_txt, 'w') as f:
            for sp in segment_paths:
                f.write(f"file '{sp}'\n")

        combined_path = os.path.join(tmpdir, 'combined.mp4')
        subprocess.run([
            'ffmpeg', '-y', '-f', 'concat', '-safe', '0',
            '-i', concat_txt,
            '-c', 'copy',           # already encoded identically, just mux
            combined_path
        ], check=True)

        # Mux video with audio, hard-trimmed to the exact voiceover length
        output_path = os.path.join(tmpdir, 'final.mp4')
        subprocess.run([
            'ffmpeg', '-y',
            '-i', combined_path,
            '-i', audio_path,
            '-t', str(total_duration),
            '-c:v', 'copy',
            '-c:a', 'aac',
            '-shortest',
            output_path
        ], check=True)

        with open(output_path, 'rb') as f:
            video_data = f.read()

    response_path = f'/tmp/final_{title[:20]}.mp4'
    with open(response_path, 'wb') as f:
        f.write(video_data)

    return send_file(response_path, mimetype='video/mp4', as_attachment=True, download_name='short.mp4')


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
