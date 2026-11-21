"""Descriptor-relative access to managed workspace files, including HTTP snapshots."""
import os
import shutil
import stat
import tempfile
import uuid
from contextlib import ExitStack, contextmanager
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from ..runtime.sandbox.workspace_snapshot import _parent_fd


@contextmanager
def parent(root: Path, path: Path, *, create=False):
    # Do not resolve again: a previously checked path may now contain a symlink.
    with ExitStack() as stack:
        try:
            relative = path.relative_to(root).as_posix()
            anchor = os.open(root.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            stack.callback(os.close, anchor)
            root_fd, _ = stack.enter_context(_parent_fd(anchor, root.relative_to(root.anchor).as_posix() + '/_'))
            entry = stack.enter_context(_parent_fd(root_fd, relative, create=create))
        except (OSError, ValueError) as exc:
            raise HTTPException(404, 'Workspace file unavailable') from exc
        yield entry


@contextmanager
def opened(root, path):
    with parent(root, path) as (directory, name):
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        except OSError as exc:
            raise HTTPException(404, 'Workspace file unavailable') from exc
        with os.fdopen(fd, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise HTTPException(400, 'Expected a regular file')
            yield stream


def read(root, path):
    with opened(root, path) as stream:
        return stream.read()


def snapshot(root, path):
    # Private response copy preserves FileResponse ranges without reopening a mutable path.
    with opened(root, path) as source:
        with tempfile.NamedTemporaryFile(prefix='tcode-response-', suffix=path.suffix, delete=False) as target:
            result = Path(target.name)
            try:
                shutil.copyfileobj(source, target)
            except BaseException:
                result.unlink(missing_ok=True)
                raise
    return result


class SnapshotResponse(FileResponse):
    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            Path(self.path).unlink(missing_ok=True)


def response(root, path, **kwargs):
    copy = snapshot(root, path)
    return SnapshotResponse(copy, background=BackgroundTask(copy.unlink, missing_ok=True), **kwargs)


def change(root, path, name=None):
    with parent(root, path) as (directory, old):
        if not stat.S_ISREG(os.stat(old, dir_fd=directory, follow_symlinks=False).st_mode):
            raise HTTPException(400, 'Expected a regular file')
        if name is not None:
            try:
                os.link(old, name, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
            except FileExistsError as exc:
                raise HTTPException(409, 'A file with that name already exists') from exc
        os.unlink(old, dir_fd=directory)


@contextmanager
def publish(root, path, source):
    with parent(root, path, create=True) as (directory, name):
        temporary = '.upload-' + uuid.uuid4().hex
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        published = False
        try:
            with os.fdopen(fd, 'wb') as output, source.open('rb') as incoming:
                shutil.copyfileobj(incoming, output)
            while True:
                try:
                    os.link(temporary, name, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
                    published = True
                    break
                except FileExistsError:
                    name = f'{path.stem}-{uuid.uuid4().hex}{path.suffix}'
            try:
                yield path.with_name(name)
            except BaseException:
                if published:
                    os.unlink(name, dir_fd=directory)
                raise
        finally:
            os.unlink(temporary, dir_fd=directory)
