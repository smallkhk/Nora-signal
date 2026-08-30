import os
import base64
import shutil


def list_dir(path):
    try:
        path = os.path.normpath(path)
        entries = []
        for name in sorted(os.listdir(path), key=lambda n: (not os.path.isdir(os.path.join(path, n)), n.lower())):
            full = os.path.join(path, name)
            try:
                st = os.stat(full)
                entries.append({
                    "name": name,
                    "type": "dir" if os.path.isdir(full) else "file",
                    "size": st.st_size,
                    "modified": int(st.st_mtime),
                })
            except Exception:
                pass
        return {"path": path, "files": entries, "error": None}
    except Exception as e:
        return {"path": path, "files": [], "error": str(e)}


def read_file(path):
    try:
        size = os.path.getsize(path)
        if size > 20 * 1024 * 1024:
            return {"path": path, "name": os.path.basename(path), "data": None, "error": "File too large (>20 MB)"}
        with open(path, "rb") as f:
            data = f.read()
        return {"path": path, "name": os.path.basename(path), "data": base64.b64encode(data).decode(), "error": None}
    except Exception as e:
        return {"path": path, "name": os.path.basename(path), "data": None, "error": str(e)}


def write_file(path, b64data):
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "wb") as f:
            f.write(base64.b64decode(b64data))
        return {"ok": True, "error": None}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def delete_path(path):
    try:
        if os.path.isdir(path):
            shutil.rmtree(path)
        else:
            os.remove(path)
        return {"ok": True, "error": None}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def home_dir():
    return os.path.expanduser("~")
