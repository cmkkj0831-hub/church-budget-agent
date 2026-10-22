"""hwp(OLE)·hwpx(zip) 문서에서 문단 텍스트를 뽑는다."""
import re
import struct
import zipfile
import zlib
from pathlib import Path

import olefile

HWPTAG_PARA_TEXT = 67
# 8워드(16바이트)를 차지하는 확장·인라인 제어 문자
_WIDE_CONTROLS = {1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23}


def _hwp_para_text(raw: bytes) -> str:
    out, j = [], 0
    while j < len(raw):
        c = struct.unpack_from("<H", raw, j)[0]
        if c < 32:
            if c in (10, 13):
                out.append("\n")
            elif c == 9:
                out.append("\t")
            j += 16 if c in _WIDE_CONTROLS else 2
            continue
        out.append(chr(c))
        j += 2
    return "".join(out)


def _hwp_lines(path) -> list[str]:
    with olefile.OleFileIO(str(path)) as f:
        compressed = f.openstream("FileHeader").read()[36] & 1
        sections = sorted((e for e in f.listdir() if e[0] == "BodyText"),
                          key=lambda e: int(e[1][len("Section"):]))
        lines = []
        for s in sections:
            data = f.openstream(s).read()
            if compressed:
                data = zlib.decompress(data, -15)
            i = 0
            while i < len(data):
                header = struct.unpack_from("<I", data, i)[0]
                i += 4
                tag, size = header & 0x3FF, header >> 20
                if size == 0xFFF:
                    size = struct.unpack_from("<I", data, i)[0]
                    i += 4
                if tag == HWPTAG_PARA_TEXT:
                    lines.append(_hwp_para_text(data[i:i + size]))
                i += size
    return lines


def _hwpx_lines(path) -> list[str]:
    lines = []
    with zipfile.ZipFile(path) as z:
        for name in sorted(n for n in z.namelist() if re.match(r"Contents/section\d+\.xml$", n)):
            xml = z.read(name).decode("utf-8")
            for p in re.findall(r"<hp:p\b.*?</hp:p>", xml, re.S):
                lines.append("".join(re.findall(r"<hp:t>([^<]*)</hp:t>", p)))
    return lines


def extract_text(path) -> str:
    path = Path(path)
    lines = _hwpx_lines(path) if path.suffix.lower() == ".hwpx" else _hwp_lines(path)
    return "\n".join(l.strip() for l in lines if l.strip())
