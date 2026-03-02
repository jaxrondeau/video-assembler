import os
import subprocess
import requests
import tempfile
from flask import Flask, request, jsonify, send_file

app = Flask(__name__)

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok"})

@app.route('/assemble', methods=['POST'])
def assemble():
    data = request.json
    audio_url = data.get('audioUrl')
    video_urls = data.get('videoUrls', [])
    title = data.get('title', 'video')

    if not audio_url or not video_urls:
        return jsonify({"error": "Missing audioUrl or videoUrls"}), 400

    with tempfile.TemporaryDirectory() as tmpdir:
        # Download audio
        audio_path = os.path.join(tmpdir, 'audio.mp3')
        r = requests.get(audio_url)
        with open(audio_path, 'wb') as f:
            f.write(r.content)

        # Download video clips
        clip_paths = []
        for i, url in enumerate(video_urls):
            clip_path = os.path.join(tmpdir, f'clip_{i}.mp4')
            r = requests.get(url, stream=True)
            with open(clip_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
            clip_paths.append(clip_path)

        # Create concat file
        concat_path = os.path.join(tmpdir, 'concat.txt')
        with open(concat_path, 'w') as f:
            for clip in clip_paths:
                f.write(f"file '{clip}'\n")

        # Concatenate clips
        combined_path = os.path.join(tmpdir, 'combined.mp4')
        subprocess.run([
            'ffmpeg', '-y', '-f', 'concat', '-safe', '0',
            '-i', concat_path,
            '-vf', 'scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920',
            '-c:v', 'libx264', '-preset', 'fast',
            '-an', combined_path
        ], check=True)

        # Get audio duration
        result = subprocess.run([
            'ffprobe', '-v', 'error', '-show_entries',
            'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1',
            audio_path
        ], capture_output=True, text=True)
        duration = float(result.stdout.strip())

        # Merge audio with video, trim to audio length
        output_path = os.path.join(tmpdir, 'final.mp4')
        subprocess.run([
            'ffmpeg', '-y',
            '-stream_loop', '-1', '-i', combined_path,
            '-i', audio_path,
            '-t', str(duration),
            '-c:v', 'libx264', '-preset', 'fast',
            '-c:a', 'aac', '-shortest',
            output_path
        ], check=True)

        # Return the file
        with open(output_path, 'rb') as f:
            video_data = f.read()

    response_path = f'/tmp/final_{title[:20]}.mp4'
    with open(response_path, 'wb') as f:
        f.write(video_data)

    return send_file(response_path, mimetype='video/mp4', as_attachment=True, download_name='short.mp4')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
