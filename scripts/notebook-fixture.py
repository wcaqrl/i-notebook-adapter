#!/usr/bin/env python3
"""Disposable real-schema fixtures and HTTP/API verification, never production data."""

import argparse
import base64
import hashlib
import io
import json
from pathlib import Path
import secrets
import sqlite3
import tarfile
import time
import urllib.error
import urllib.request
import zipfile

BASELINE_URL = "https://lzc-download-1301583638.cos.ap-guangzhou.myqcloud.com/appstore/lpks/pkgs/com.luck.lazycat.notebook/com.luck.lazycat.notebook-v0.0.1.lpk"
BASELINE_SHA256 = "42c9bda59aa0c3d715dc7d3256d5e315f14144a5d7b01d01513d641256827538"
PARENT = "lzcparent001"
CHILD = "lzcchild0001"
TITLE = "懒猫升级验证：中文笔记"
TEXT = "保留旧版正文与目录层级 LazyCat upgrade fixture"
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jWZkAAAAASUVORK5CYII=")


def read_state(root):
    return json.loads((root / "fixture.json").read_text())


def write_state(root, state):
    path = root / "fixture.json"
    path.write_text(json.dumps(state, ensure_ascii=False))
    path.chmod(0o600)


def request(url, data=None, headers=None):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(req, timeout=40) as response:
        return response.read()


def api(base, state, path, payload=None):
    headers = {"Authorization": "Bearer " + state["api_key"]}
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode()
        headers["Content-Type"] = "application/json"
    result = json.loads(request(base + path, data, headers))
    if result.get("code") != 0:
        raise ValueError(f"Application API failed for {path}: {result.get('msg')}")
    return result["data"]


def configure_api(root, db):
    path = root / "fixture.json"
    state = read_state(root) if path.exists() else {"api_key": secrets.token_urlsafe(32)}
    settings = {
        "openApiEnabled": "true",
        "openApiKeys": json.dumps([{"id": "adapter-test", "name": "Disposable Runner test", "apiKeyHash": hashlib.sha256(state["api_key"].encode()).hexdigest()}]),
    }
    for index, (key, value) in enumerate(settings.items()):
        db.execute('DELETE FROM "Config" WHERE "key"=?', (key,))
        db.execute('INSERT INTO "Config" (id,key,value,type) VALUES (?,?,?,?)', (f"lzccfg{index:06}", key, value, "string"))
    write_state(root, state)


def lexical_document():
    return {"root": {"type": "root", "version": 1, "direction": "ltr", "format": "", "indent": 0, "children": [
        {"type": "paragraph", "version": 1, "direction": "ltr", "format": "", "indent": 0, "textFormat": 0, "textStyle": "", "children": [
            {"type": "text", "version": 1, "detail": 0, "format": 0, "mode": "normal", "style": "", "text": TEXT}
        ]}
    ]}}


def init_old(root):
    blob = request(BASELINE_URL)
    if hashlib.sha256(blob).hexdigest() != BASELINE_SHA256:
        raise ValueError("Approved baseline LPK checksum changed")
    with zipfile.ZipFile(io.BytesIO(blob)) as package:
        with tarfile.open(fileobj=io.BytesIO(package.read("content.tar"))) as content:
            # Extract the one known regular DB; never extract a remote archive wholesale.
            member = next(m for m in content.getmembers() if m.name.lstrip("./") == "db.sqlite" and m.isfile())
            (root / "data/db.sqlite").write_bytes(content.extractfile(member).read())
    with sqlite3.connect(root / "data/db.sqlite") as db:
        content = json.dumps(lexical_document(), ensure_ascii=False)
        for id_, title, parent in ((PARENT, "懒猫测试目录", None), (CHILD, TITLE, PARENT)):
            db.execute('INSERT INTO "Note" (id,title,content,pId,sortOrder,createdAt,updatedAt,starAt) VALUES (?,?,?,?,?,?,?,?)',
                       (id_, title, content, parent, 7, 1747000000000, 1747000000000, 1747000000000))
        db.execute('INSERT INTO "NoteHistory" (id,noteId,noteTitle,content,createdAt) VALUES (?,?,?,?,?)',
                   ("lzchistory01", CHILD, TITLE, content, 1747000000000))
        configure_api(root, db)
    print("Prepared approved 0.0.1 LPK database with 0.7.1 schema, nested Chinese notes, history and settings.")


def upload(base):
    boundary = "LazyCatAdapter" + secrets.token_hex(12)
    data = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"fixture.png\"\r\nContent-Type: image/png\r\n\r\n".encode()
            + PNG + f"\r\n--{boundary}--\r\n".encode())
    result = json.loads(request(base + "/api/upload", data, {"Content-Type": "multipart/form-data; boundary=" + boundary}))
    if result.get("code") != 0:
        raise ValueError("Real application attachment upload failed")
    return result["data"]


def old_upload(root, base):
    state = read_state(root)
    result = upload(base)
    url = result["url"]
    if not url.startswith("/uploads/") or (root / "data/uploads" / url.removeprefix("/uploads/")).read_bytes() != PNG:
        raise ValueError("Old upload API did not persist the attachment")
    state["old_attachment"] = url.removeprefix("/uploads/")
    doc = lexical_document()
    # Match the real Lexical image serialization: an inline image lives in a
    # paragraph and always carries its nested caption editor, even if hidden.
    # Omitting caption.editorState produces an invalid node that the Markdown
    # exporter silently drops; that would test a malformed fixture, not upgrades.
    doc["root"]["children"].append({
        "type": "paragraph", "version": 1, "direction": "ltr", "format": "", "indent": 0,
        "textFormat": 0, "textStyle": "", "children": [{
            "type": "image", "version": 1, "src": url, "altText": "升级图片",
            "width": 1, "height": 1, "maxWidth": 500, "showCaption": False,
            "caption": {"editorState": {"root": {
                "type": "root", "version": 1, "direction": None, "format": "", "indent": 0, "children": []
            }}},
        }],
    })
    with sqlite3.connect(root / "data/db.sqlite") as db:
        db.execute('UPDATE "Note" SET content=? WHERE id=?', (json.dumps(doc, ensure_ascii=False), CHILD))
    write_state(root, state)
    print("Uploaded a real attachment through the old API and verified its persisted bytes.")


def check_database(root, legacy):
    with sqlite3.connect(root / "data/db.sqlite") as db:
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",), "SQLite corruption"
        assert not db.execute("PRAGMA foreign_key_check").fetchall(), "Broken relationships"
        if legacy:
            title, content, parent, sort_order, star = db.execute('SELECT title,content,pId,sortOrder,starAt FROM Note WHERE id=?', (CHILD,)).fetchone()
            assert title == TITLE and parent == PARENT and sort_order == 7 and star, "Lost note metadata"
            assert TEXT in content and read_state(root)["old_attachment"] in content, "Lost note content"
            assert db.execute('SELECT noteTitle FROM NoteHistory WHERE id=?', ("lzchistory01",)).fetchone() == (TITLE,), "Lost history"
        assert db.execute('SELECT value FROM Config WHERE key="openApiEnabled"').fetchone() == ("true",)
        assert db.execute('SELECT count(*) FROM i_notebook_migrations').fetchone()[0] >= 1
    print("SQLite integrity, migration records and persisted application data verified.")


def verify(root, base, legacy):
    state = read_state(root)
    page = request(base + "/start").decode()
    assert "<html" in page.lower(), "Missing application UI"
    # Real API authentication, including rejection of anonymous access.
    try:
        request(base + "/api/get-page?id=" + CHILD)
    except urllib.error.HTTPError as error:
        assert error.code == 401
    else:
        raise ValueError("API accepted an unauthenticated request")
    if legacy:
        note = api(base, state, "/api/get-page?id=" + CHILD)
        assert note["title"] == TITLE and TEXT in note["content"], "Upgraded application cannot read old notes"
        # HTML is the rendered image contract; an API Markdown exporter may
        # omit decorator nodes. Also check the persisted JSON independently.
        rendered = api(base, state, "/api/get-page?id=" + CHILD + "&format=html")
        if state["old_attachment"] not in rendered["content"] or "<img" not in rendered["content"]:
            with sqlite3.connect(root / "data/db.sqlite") as db:
                stored = db.execute('SELECT content FROM Note WHERE id=?', (CHILD,)).fetchone()[0]
            raise ValueError("Old image rendering failed; stored attachment=" + str(state["old_attachment"] in stored)
                             + "; Markdown=" + repr(note["content"][:1200]) + "; HTML=" + repr(rendered["content"][:1600]))
        # /uploads/<key> is forwarded here by the LazyCat manifest compatibility route.
        assert request(base + "/api/file/" + state["old_attachment"]) == PNG
    if "new_note" not in state:
        payload = {"title": "新版写入验证", "content": "升级后可以创建和读取笔记 New version write test"}
        if legacy:
            payload["pId"] = PARENT
        created = api(base, state, "/api/create-page", payload)
        state["new_note"] = created["id"]
        attachment = upload(base)
        state["new_attachment"] = attachment["filename"]
        write_state(root, state)
    note = api(base, state, "/api/get-page?id=" + state["new_note"])
    assert "New version write test" in note["content"]
    assert request(base + "/api/file/" + state["new_attachment"]) == PNG
    check_database(root, legacy)
    print("Real UI, authenticated note reads/writes, attachment upload/download and persistence verified.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("init-old", "old-upload", "old-download", "configure-api", "verify-old", "verify-fresh"))
    parser.add_argument("root", type=Path)
    parser.add_argument("base", nargs="?")
    args = parser.parse_args()
    if args.operation == "init-old":
        init_old(args.root)
    elif args.operation == "configure-api":
        with sqlite3.connect(args.root / "data/db.sqlite") as db:
            configure_api(args.root, db)
    elif args.operation == "old-upload":
        old_upload(args.root, args.base)
    elif args.operation == "old-download":
        state = read_state(args.root)
        assert request(args.base + "/uploads/" + state["old_attachment"]) == PNG
        print("Old server downloaded the uploaded attachment after recreation.")
    else:
        verify(args.root, args.base, args.operation == "verify-old")


if __name__ == "__main__":
    main()
