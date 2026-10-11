import support, os, tempfile, time, threading, shutil, glob
from pathlib import Path
from support import ok
os.environ["WIREPLAY_CACHE"] = tempfile.mkdtemp(prefix="wp_thumbs_", dir="/tmp")
from PySide6.QtGui import QImage, QColor, QGuiApplication, QPainter
from PySide6.QtCore import QSize
app = QGuiApplication([])
import gui
src = Path(tempfile.mkdtemp(prefix="wp_art_", dir="/tmp"))
def make(name, w, h, alpha=False):
    img = QImage(w, h, QImage.Format_ARGB32 if alpha else QImage.Format_RGB32); img.fill(QColor(30, 120, 220, 255 if not alpha else 0))
    p = QPainter(img)                                   # noisy so the file is large like real art
    import random; random.seed(1)
    for _ in range(4000): p.fillRect(random.randrange(w), random.randrange(h), 12, 12, QColor(random.randrange(256), random.randrange(256), random.randrange(256), 255))
    p.end(); path = src / name; img.save(str(path), "PNG"); return path
big = make("big.png", 1000, 1500); small = make("small.png", 60, 90); alpha = make("alpha.png", 900, 1350, alpha=True)
ok(big.stat().st_size > 64 << 10 and small.stat().st_size < 64 << 10, f"fixtures: big {big.stat().st_size // 1024} KB, small {small.stat().st_size // 1024} KB")
thumbs = lambda: sorted(p for p in gui.THUMB_DIR.glob("*.*") if not p.name.startswith(".")) if gui.THUMB_DIR.exists() else []
size = QSize(160, 240)
t = time.perf_counter(); a = gui.load_scaled(big, size); cold = (time.perf_counter() - t) * 1000
ok(not a.isNull() and a.size() == size and len(thumbs()) == 1, f"first load decodes the original ({cold:.1f} ms) and writes one thumbnail ({thumbs()[0].stat().st_size // 1024} KB)")
t = time.perf_counter(); b = gui.load_scaled(big, size); warm = (time.perf_counter() - t) * 1000
ok(b.size() == size and len(thumbs()) == 1, f"second load comes from the thumbnail ({warm:.1f} ms vs {cold:.1f} ms cold)")
pts = [(x, y) for x in range(0, 160, 3) for y in range(0, 240, 3)]
diff = sum(abs(a.pixelColor(x, y).red() - b.pixelColor(x, y).red()) for x, y in pts) / len(pts)
ok(diff < 20, f"cached result matches the direct one (mean red diff {diff:.1f}/255 on worst-case random noise; JPEG q90)")
c = gui.load_scaled(big, QSize(150, 225)); n = len(thumbs())
ok(c.size() == QSize(150, 225) and n == 1, "a slightly different zoom reuses the same thumbnail (bucketed), output is the requested size")
d = gui.load_scaled(big, QSize(300, 450))
ok(d.size() == QSize(300, 450) and len(thumbs()) == 2, "a much larger size gets its own bucket")
gui.load_scaled(small, size); ok(len(thumbs()) == 2, "small originals are not duplicated")
t = time.perf_counter(); al1 = gui.load_scaled(alpha, size); c_ms = (time.perf_counter() - t) * 1000
ok(len(thumbs()) == 3 and thumbs()[-1].suffix in (".png", ".jpg") and any(p.suffix == ".png" for p in thumbs()), "art with transparency is cached as a lossless PNG thumbnail")
t = time.perf_counter(); al2 = gui.load_scaled(alpha, size); w_ms = (time.perf_counter() - t) * 1000
ok(al2.hasAlphaChannel() and al2.pixelColor(80, 120).alpha() in range(0, 256) and al1.size() == al2.size() == size, f"…and keeps its alpha channel when read back ({w_ms:.1f} ms vs {c_ms:.1f} ms cold)")
os.utime(big, (time.time() + 5, time.time() + 5)); e = gui.load_scaled(big, size)
ok(len(thumbs()) == 4 and e.size() == size, "changed art (new mtime) gets a fresh thumbnail, never a stale one")
for t_ in thumbs(): t_.write_bytes(b"garbage")
f = gui.load_scaled(big, size)
ok(not f.isNull() and f.size() == size, "corrupt thumbnails are ignored and the original is used")
ok(gui.load_scaled(src / "missing.png", size).isNull(), "a missing file gives a null image, no exception")
# concurrent loads of the same art
res = []
def work(): res.append(gui.load_scaled(big, QSize(260, 390)))
ts = [threading.Thread(target=work) for _ in range(8)]; [t.start() for t in ts]; [t.join() for t in ts]
ok(len(res) == 8 and all(not r.isNull() and r.size() == QSize(260, 390) for r in res) and not list(gui.THUMB_DIR.glob(".*part*")), "8 threads loading the same poster at once: all fine, no leftover temp files")
print("ALL PASS")
