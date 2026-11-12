"""Bounded MJPEG frame parsing shared by the Windows viewer and tests."""
def read_frame(stream):
    length = 0
    for _ in range(64):
        line = stream.readline(8193)
        if not line:
            raise EOFError("stream ended before next frame")
        if len(line) > 8192:
            raise ValueError("frame header too long")
        if line.lower().startswith(b"content-length:"):
            length = int(line.split(b":", 1)[1].strip())
        if not line.strip() and length:
            break
    else:
        raise ValueError("too many frame headers")
    if length < 1 or length > 12000000:
        raise ValueError("invalid frame length")
    data = stream.read(length)
    if len(data) != length:
        raise EOFError("partial frame")
    return data
