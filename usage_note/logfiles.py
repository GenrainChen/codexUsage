"""Stream plain and Zstandard rollout metadata without unpacking files to disk."""

from pathlib import Path

import zstandard


def is_rollout_path(path: Path) -> bool:
    return path.name.endswith(('.jsonl', '.jsonl.zst'))


def compressed_lines(stream, size: int):
    """Yield complete JSONL lines across frames, rejecting truncated archives.

    Read only the captured compressed size. Retain partial lines across frames
    and chunks, and leave a final unfinished line uncounted like plain logs.
    """
    remaining, frames = size, 0
    decoder = None
    pending = []
    try:
        while remaining:
            chunk = stream.read(min(65536, remaining))
            if not chunk:
                raise OSError('Compressed usage log changed during read')
            remaining -= len(chunk)
            while chunk:
                if decoder is None:
                    decoder = zstandard.ZstdDecompressor().decompressobj()
                decoded = decoder.decompress(chunk)
                parts = decoded.split(b'\n')
                if len(parts) > 1:
                    yield b''.join([*pending, parts[0], b'\n'])
                    for line in parts[1:-1]:
                        yield line + b'\n'
                    pending = [parts[-1]] if parts[-1] else []
                elif decoded:
                    pending.append(decoded)
                if decoder.eof:
                    chunk = decoder.unused_data
                    decoder = None
                    frames += 1
                else:
                    chunk = b''
        if decoder is not None or not frames:
            raise OSError('Compressed usage log is incomplete')
    except zstandard.ZstdError:
        raise OSError('Compressed usage log is invalid') from None
