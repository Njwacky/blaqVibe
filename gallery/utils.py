import zipfile, os, json
from pathlib import Path

def build_tree_from_zip(zip_path):
    """Return (tree_dict, file_list) where file_list = [{'path':..., 'size':...}]"""
    file_list = []
    tree = {}
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            path = info.filename
            file_list.append({'path': path, 'size': info.file_size})
            parts = path.strip('/').split('/')
            node = tree
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = None
    return tree, file_list

def ensure_readme_in_zip(zip_path, readme_text):
    """If ZIP has no README.md, create one. Returns True if added."""
    has_readme = False
    with zipfile.ZipFile(zip_path, 'r') as z:
        for n in z.namelist():
            if n.lower().endswith('readme.md'):
                has_readme = True
                break
    if not has_readme and readme_text:
        return False
    return has_readme
