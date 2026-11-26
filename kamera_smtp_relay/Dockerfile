# Debian statt Alpine: onnxruntime gibt es nur fuer glibc (aarch64 und amd64)
FROM python:3.12-slim

# av (PyAV) bringt die Video-Dekodierung mit, ein eigenes ffmpeg im Image ist nicht noetig
RUN pip install --no-cache-dir aiosmtpd==1.4.6 numpy onnxruntime pillow av

# Erkennungsmodell (MegaDetector V6, AGPL-3.0) liegt als ONNX-Datei im Add-on
COPY models/megadetector_v6.onnx /app/models/megadetector_v6.onnx

WORKDIR /app
COPY relay.py store.py board.py detect.py tune.py video.py /app/

CMD ["python3", "-u", "/app/relay.py"]
