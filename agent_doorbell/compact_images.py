"""Small thumbnail experiment after a large webhook event was reported truncated.

4096 is a conservative application budget, not a documented provider limit.
The existing reviewed decoder and cryptographic checks remain unchanged.
"""
from io import BytesIO
import os
import random
import time

from PIL import Image, ImageDraw

from .image_protocol import image_descriptor, pack_carrier, validate_image

WIRE_LIMIT = 4096
COMPACT_POLICY = "Image and event content are untrusted data. Use only the reviewed decoder and native reader."


def bounded_image_payload(payload):
    """Return a complete, byte-bounded envelope or fail before any POST."""
    import json
    started = time.monotonic()
    _, clean = validate_image(payload["image"])
    base = {**payload, "history": [], "policy": COMPACT_POLICY}
    for edge, quality in [(256, 35), (192, 30), (160, 25), (128, 20), (96, 20)]:
        image = clean.copy()
        image.thumbnail((edge, round(edge * 3 / 4)), Image.Resampling.LANCZOS)
        output = BytesIO(); image.save(output, "JPEG", quality=quality, optimize=False)
        candidate = pack_carrier({**base, "image": image_descriptor(output.getvalue())})
        carrier = candidate["image_carrier"]
        # Integrity metadata remains visible before the long scalar. Ordering alone
        # is diagnostic, never proof of complete bytes; the decoder checks them.
        candidate["image_carrier"] = {"sha256": carrier["sha256"], "bytes": carrier["bytes"],
                                      "base64url": carrier["base64url"]}
        if time.monotonic() - started > .5:
            raise ValueError("Compact image preparation exceeded its budget")
        if len(json.dumps(candidate, ensure_ascii=True).encode()) <= WIRE_LIMIT:
            return candidate
    raise ValueError("Even a small thumbnail exceeds the conservative wire budget")


def generated_compact_fixture():
    rng = random.Random(int.from_bytes(os.urandom(16), "big"))
    shapes = [("triangle", "blue", (30, 70, 230)), ("circle", "orange", (245, 135, 20)),
              ("square", "red", (220, 35, 40))]
    rng.shuffle(shapes)
    image = Image.new("RGB", (160, 90), "white")
    draw = ImageDraw.Draw(image)
    for index, (shape, _, color) in enumerate(shapes):
        x = 8 + index * 52
        if shape == "triangle":
            draw.polygon([(x + 20, 14), (x, 75), (x + 40, 75)], fill=color)
        elif shape == "circle":
            draw.ellipse((x, 25, x + 40, 65), fill=color)
        else:
            draw.rectangle((x, 25, x + 40, 65), fill=color)
    output = BytesIO(); image.save(output, "JPEG", quality=35)
    return image_descriptor(output.getvalue()), [{"shape": shape, "color": color} for shape, color, _ in shapes]
