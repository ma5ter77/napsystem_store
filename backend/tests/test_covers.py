"""NAP-025: app covers — validated, cleaned and normalized on upload, moderated with the version, public on publish."""
import hashlib
import io
import uuid

from .conftest import Client, ok
from .helpers import convert, upload, wav_bytes


def _img(w, h, fmt="JPEG", exif_gps=False, mode="RGB"):
    from PIL import Image
    im = Image.new(mode, (w, h), (200, 80, 40) if mode == "RGB" else (200, 80, 40, 128))
    buf = io.BytesIO()
    kw = {}
    if exif_gps:
        ex = Image.Exif()
        ex[0x010F] = "SecretCam"                  # Make
        ex[0x8825] = {1: "N", 2: (55.0, 45.0, 0.0)}  # GPS info
        kw["exif"] = ex.tobytes()
    im.save(buf, fmt, **kw)
    return buf.getvalue()


def _cover(dev, app_id, data, name):
    t = ok(dev.post(f"/developer/apps/{app_id}/uploads", {"filename": name, "size_bytes": len(data),
                                                           "sha256": hashlib.sha256(data).hexdigest(), "kind": "cover"}), 201)
    return t, dev.raw().put(t["upload_url"], content=data, headers=dev._h({"content-type": "application/octet-stream"}))


def test_cover_upload_moderation_and_public_url(tmp_path):
    from PIL import Image
    dev, mod, anon = Client().register(developer=True), Client().login("moderator@nap.demo"), Client()
    slug = "cv-" + uuid.uuid4().hex[:8]
    a = ok(dev.post("/developer/apps", {"slug": slug, "category": "meditation", "author_name": "A",
                                        "metadata": {"ru": {"title": "Обложка"}}, "content_language": "ru"}), 201)
    v = a["version_id"]
    # bad files are rejected with a clear code
    _, r = _cover(dev, a["id"], b"not an image at all" * 100, "fake.jpg")
    assert r.status_code == 422 and r.json()["error"]["code"] == "cover_invalid"
    _, r = _cover(dev, a["id"], _img(400, 300), "small.jpg")
    assert r.status_code == 422 and r.json()["error"]["code"] == "cover_too_small"
    assert dev.post(f"/developer/apps/{a['id']}/uploads", {"filename": "x.gif", "size_bytes": 10, "kind": "cover"}).status_code == 415
    # a wide photo with GPS EXIF: stored as clean 1200x900 WebP
    t, r = _cover(dev, a["id"], _img(2000, 1000, exif_gps=True), "photo.jpg")
    assert r.status_code == 200, r.text
    ok(dev.patch(f"/developer/apps/{a['id']}/versions/{v}", {"cover_asset_id": t["asset_id"]}))
    ver = ok(dev.get(f"/developer/apps/{a['id']}"))["versions"][0]
    assert ver["cover"]["asset_id"] == t["asset_id"]
    img = dev.raw().get(ver["cover"]["url"])
    assert img.status_code == 200
    im = Image.open(io.BytesIO(img.content))
    assert im.format == "WEBP" and im.size == (1200, 900)
    assert not im.getexif() and b"SecretCam" not in img.content
    # a cover of another app or a source file cannot be attached
    assert dev.patch(f"/developer/apps/{a['id']}/versions/{v}", {"cover_asset_id": str(uuid.uuid4())}).status_code == 422
    # program, price, submit: the manifest names the cover, the moderator sees it, it is frozen
    up = upload(dev, a["id"], wav_bytes(2.0))
    job = convert(dev, a["id"], up["ticket"]["asset_id"])
    ok(dev.patch(f"/developer/apps/{a['id']}/versions/{v}", {"confirm_rights": True}))
    ok(dev.put(f"/developer/apps/{a['id']}/versions/{v}/program",
               {"sessions": [{"key": "s1", "titles": {"ru": "С"}, "conversion_job_id": job["id"]}]}))
    ok(dev.put(f"/developer/apps/{a['id']}/price", {"kind": "free"}))
    ok(dev.post(f"/developer/apps/{a['id']}/versions/{v}/submit"))
    m = ok(mod.get(f"/moderation/versions/{v}"))
    assert m["manifest"]["cover"]["asset_id"] == t["asset_id"] and m["version"]["cover"]["url"]
    assert m["integrity"] == []
    assert dev.patch(f"/developer/apps/{a['id']}/versions/{v}", {"cover_asset_id": None}).status_code in (409, 422)
    # not public before publishing
    assert anon.get(f"/catalog/apps/{slug}/cover").status_code == 404
    r = ok(mod.post(f"/moderation/versions/{v}/decision", {"decision": "approve", "comment": ""}))
    assert r["state"] == "published"
    card = ok(anon.get(f"/catalog/apps/{slug}"))
    assert card["cover_url"].startswith(f"/api/v1/catalog/apps/{slug}/cover?v=")
    pub = anon.raw().get(card["cover_url"])
    assert pub.status_code == 200 and pub.headers["content-type"] == "image/webp"
    assert "immutable" in pub.headers["cache-control"]
    # the next version inherits the cover and can replace it
    nv = ok(dev.post(f"/developer/apps/{a['id']}/versions"), 201)
    assert nv["cover"]["asset_id"] == t["asset_id"]


def test_transparent_png_is_flattened():
    from PIL import Image
    dev = Client().register(developer=True)
    a = ok(dev.post("/developer/apps", {"slug": "cvp-" + uuid.uuid4().hex[:8], "category": "meditation", "author_name": "A",
                                        "metadata": {"ru": {"title": "P"}}, "content_language": "ru"}), 201)
    t, r = _cover(dev, a["id"], _img(1200, 900, "PNG", mode="RGBA"), "logo.png")
    assert r.status_code == 200, r.text
    ok(dev.patch(f"/developer/apps/{a['id']}/versions/{a['version_id']}", {"cover_asset_id": t["asset_id"]}))
    url = ok(dev.get(f"/developer/apps/{a['id']}"))["versions"][0]["cover"]["url"]
    im = Image.open(io.BytesIO(dev.raw().get(url).content))
    assert im.mode == "RGB" and im.size == (1200, 900)
