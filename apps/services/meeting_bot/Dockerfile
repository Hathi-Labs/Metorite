# Metorite Meeting Bot — headless-Chrome meeting-joining worker.
# Based on Playwright's Python image (bundles Chromium + all its system libs),
# plus PulseAudio + ffmpeg + Xvfb for the audio-capture pipeline.
FROM mcr.microsoft.com/playwright/python:v1.48.0-jammy

# x11vnc + noVNC let you WATCH the bot's real browser (and finish a Google
# sign-in by hand). Off unless MEET_VNC=1 — see entrypoint.sh. Without this the
# only window into a join failure is a post-mortem screenshot, which is no way
# to debug a UI that isn't a public API.
#
# DEBIAN_FRONTEND=noninteractive is load-bearing, not boilerplate: novnc pulls
# in tzdata, whose postinst asks "Geographic area:" on a terminal. In a docker
# build that prompt waits forever — the build hangs, and (seen in production)
# the deploy's SSH session dies 15 minutes later while the old image stays live.
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive TZ=Etc/UTC apt-get install -y \
        --no-install-recommends \
        pulseaudio ffmpeg xvfb x11vnc novnc websockify \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

ENV MEETING_BOT_DATA=/data \
    PULSE_MONITOR=meet.monitor \
    DISPLAY=:99
VOLUME ["/data"]
# 8080 API · 6080 noVNC (only listening when MEET_VNC=1)
EXPOSE 8080 6080

CMD ["./entrypoint.sh"]
