"""Pinned ARM runtime loader. No download, install, shell, build or network."""
import hashlib
import importlib
import os
from pathlib import Path
import stat
import sys

WHEEL_SHA256 = 'b37227bb2ca9d647fd056d33f496716d3436a4aa4a027434e50a7192a1d7d567'
RUNTIME_PATH = Path('/opt/mailpilot-protected/runtime/sqlcipher3-0.6.5-cp312-aarch64')
EXPECTED_FILES = {'sqlcipher3/__init__.py': '3e9fb2097abc3d7802d06a03faba070eeef8274412fed70293aa3b421bbb6a8e', 'sqlcipher3/_sqlite3.cpython-312-aarch64-linux-gnu.so': '8a5d7afa4c034ced27042436f0e532f0b650787fdc05e713878364abdcab2408', 'sqlcipher3/dbapi2.py': '8fa5c6ae7b32600a601c164c75e2a14bed13ea9d9240e4791c4dcca508262394', 'sqlcipher3-0.6.5.dist-info/METADATA': '540919536395496589136ba207ef681134efb7c74e4eef353b3b27521a7bd1f1', 'sqlcipher3-0.6.5.dist-info/WHEEL': '57e812b5c1765bb46640960d298087598dfd17e82fe73cf4d2e5e6d5c75476d5', 'sqlcipher3-0.6.5.dist-info/top_level.txt': 'c97df90fa94568ed76f8ac2220b8e1e1aae83d88eb41438cdfdbfc3fc2878652', 'sqlcipher3-0.6.5.dist-info/RECORD': '66572ba66b330bae2947e6abc856db1a59c3ac62c8390a5fef27678bf7404b02', 'sqlcipher3-0.6.5.dist-info/licenses/LICENSE': 'fa23cf250126548e90008fe92de4ee76d485bfbb3592f5be8aa731775892a960'}


def verify_runtime_tree(directory, *, owner_uid=0):
    if not isinstance(directory,Path) or not directory.is_absolute():
        raise ValueError('Explicit immutable runtime directory required.')
    for p in (directory,*directory.parents):
        st=p.lstat()
        if stat.S_ISLNK(st.st_mode) or st.st_uid not in {0,owner_uid} or st.st_mode & 0o022:
            raise ValueError('Runtime directory custody is not qualified.')
    paths=list(directory.rglob('*'))
    if any(p.is_symlink() for p in paths):
        raise ValueError('Runtime symlinks are not qualified.')
    present={str(p.relative_to(directory)) for p in paths if not p.is_dir()}
    if present!=set(EXPECTED_FILES):
        raise ValueError('Runtime inventory does not match pinned artifact.')
    for name,digest in EXPECTED_FILES.items():
        p=directory/name
        # Inspect every package subdirectory too, not just leaf files.
        for parent in p.parents:
            if parent==directory:break
            st=parent.lstat()
            if stat.S_ISLNK(st.st_mode) or st.st_uid not in {0,owner_uid} or st.st_mode & 0o022:
                raise ValueError('Runtime directory custody is not qualified.')
        fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW)
        try:
            st=os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_uid not in {0,owner_uid} or st.st_mode & 0o022:
                raise ValueError('Runtime file custody is not qualified.')
            with os.fdopen(fd,'rb',closefd=False) as f:
                h=hashlib.sha256()
                for chunk in iter(lambda:f.read(65536),b''):
                    h.update(chunk)
                actual=h.hexdigest()
            if actual!=digest:
                raise ValueError('Runtime bytes do not match pinned artifact.')
        finally:os.close(fd)


def load_reviewed_arm_driver():
    # No existing import can bypass byte/custody verification or mix artifacts.
    if any(n=='sqlcipher3' or n.startswith('sqlcipher3.') for n in sys.modules):
        raise ValueError('Cipher module is already loaded outside this bootstrap.')
    verify_runtime_tree(RUNTIME_PATH)
    sys.dont_write_bytecode=True
    sys.path.insert(0,str(RUNTIME_PATH))
    try:
        driver=importlib.import_module('sqlcipher3.dbapi2')
        for name in ('sqlcipher3','sqlcipher3.dbapi2','sqlcipher3._sqlite3'):
            module=sys.modules[name]
            if RUNTIME_PATH not in Path(module.__file__).parents:
                raise ValueError('Cipher module origin does not match pinned runtime.')
        return driver
    finally:
        sys.path.remove(str(RUNTIME_PATH))
